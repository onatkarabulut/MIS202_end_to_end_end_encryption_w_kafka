from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from confluent_kafka import Producer, Consumer, KafkaException
import base64
import json
import uvicorn

app = FastAPI()

# Kafka setup
kafka_bootstrap_servers = 'localhost:9092'
topic = 'chat_topic'

# Generating RSA key pairs
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
public_key = private_key.public_key()

# Serialization of keys
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption()
)
public_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)

# Kafka creating producers and consumers
producer = Producer({'bootstrap.servers': kafka_bootstrap_servers})
consumer = Consumer({
    'bootstrap.servers': kafka_bootstrap_servers,
    'group.id': 'chat_group',
    'auto.offset.reset': 'earliest'
})

class Message(BaseModel):
    recipient: str
    message: str

@app.get("/get_public_key")
async def get_public_key():
    return {'public_key': public_pem.decode('utf-8')}

@app.post("/send_message")
async def send_message(message: Message):
    recipient_public_key = message.recipient

    try:
        recipient_public_key = serialization.load_pem_public_key(
            recipient_public_key.encode('utf-8')
        )

        # Encrypt the message
        encrypted_message = recipient_public_key.encrypt(
            message.message.encode('utf-8'),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )

        # Send the coded message to Kafka
        producer.produce(topic, json.dumps({'message': base64.b64encode(encrypted_message).decode('utf-8')}).encode('utf-8'))
        producer.flush()
        return {"status": "success", "message": "Message sent"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/receive_message")
async def receive_message():
    # Get a message from Kafka
    msg = consumer.poll(timeout=1.0)

    if msg is None:
        return {"status": "No message received"}

    if msg.error():
        raise HTTPException(status_code=500, detail=str(msg.error()))

    # Decode the incoming message
    encrypted_message = json.loads(msg.value().decode('utf-8'))['message']
    encrypted_message = base64.b64decode(encrypted_message)

    decrypted_message = private_key.decrypt(
        encrypted_message,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    return {"status": "success", "message": decrypted_message.decode('utf-8')}

if __name__ == "__main__":
    # FastAPI start
    uvicorn.run(app, host="0.0.0.0", port=8000)
