from kafka import KafkaConsumer
import json

class KafkaConsumer:
    def __init__(self):
        self.consumer = KafkaConsumer(
            'chat_messages',
            bootstrap_servers=['localhost:9092'],
            auto_offset_reset='earliest',
            enable_auto_commit=True,
            group_id='chat-group',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )

    def consume_messages(self):
        for message in self.consumer:
            print(f"Received: {message.value}")

if __name__ == "__main__":
    consumer = KafkaConsumer()
    consumer.consume_messages()