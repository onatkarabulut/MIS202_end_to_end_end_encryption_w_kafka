from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from confluent_kafka import Producer, Consumer
from cryptography.hazmat.backends import default_backend

import base64
import json
import os
import uvicorn

app = FastAPI()

kafka_bootstrap_servers = 'localhost:9092'
topic = 'chat_topic'

producer = Producer({'bootstrap.servers': kafka_bootstrap_servers})
consumer = Consumer({
    'bootstrap.servers': kafka_bootstrap_servers,
    'group.id': 'chat_group',
    'auto.offset.reset': 'earliest'
})

server_private_key = None

class Message(BaseModel):
    recipient: str
    message: str

@app.on_event("startup")
async def startup_event():
    consumer.subscribe([topic])

@app.on_event("shutdown")
async def shutdown_event():
    consumer.close()

@app.get("/get_public_key")
async def get_public_key():

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()


    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )


    global server_private_key
    server_private_key = private_key


    return {'public_key': public_pem.decode('utf-8')}


def encrypt_message(message, recipient_public_key):

    symmetric_key = os.urandom(32)
    iv = os.urandom(16)  

    cipher = Cipher(algorithms.AES(symmetric_key), modes.CFB(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_message = encryptor.update(message.encode('utf-8')) + encryptor.finalize()

    encrypted_key = recipient_public_key.encrypt(
        symmetric_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    package = {
        'encrypted_key': base64.b64encode(encrypted_key).decode('utf-8'),
        'iv': base64.b64encode(iv).decode('utf-8'),
        'encrypted_message': base64.b64encode(encrypted_message).decode('utf-8')
    }

    return json.dumps(package)

def decrypt_message(encrypted_package):
    package = json.loads(encrypted_package)

    encrypted_key = base64.b64decode(package['encrypted_key'])
    iv = base64.b64decode(package['iv'])
    encrypted_message = base64.b64decode(package['encrypted_message'])

    symmetric_key = server_private_key.decrypt(
        encrypted_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    cipher = Cipher(algorithms.AES(symmetric_key), modes.CFB(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted_message = decryptor.update(encrypted_message) + decryptor.finalize()

    return decrypted_message.decode('utf-8')

@app.post("/send_message")
async def send_message(message: Message):
    recipient_public_key = message.recipient

    try:
        recipient_public_key = serialization.load_pem_public_key(
            recipient_public_key.encode('utf-8')
        )

        encrypted_package = encrypt_message(message.message, recipient_public_key)

        producer.produce(topic, encrypted_package.encode('utf-8'))
        producer.flush()
        return {"status": "success", "message": "Message sent"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/receive_message")
async def receive_message():
    msg = consumer.poll(timeout=1.0)

    if msg is None:
        return {"status": "No message received"}

    if msg.error():
        raise HTTPException(status_code=500, detail=str(msg.error()))

    encrypted_package = msg.value().decode('utf-8')

    try:
        decrypted_message = decrypt_message(encrypted_package)

        return {"status": "success", "message": decrypted_message}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
