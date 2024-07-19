from kafka import KafkaProducer as KafkaProducerLib
import json

class KafkaMessageProducer:
    def __init__(self, bootstrap_servers):
        self.producer = KafkaProducerLib(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )

    def send_message(self, message):
        self.producer.send('chat_messages', message)
        self.producer.flush()