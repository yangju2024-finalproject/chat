from kafka import KafkaConsumer
import json

class KafkaMessageConsumer:
    def __init__(self, bootstrap_servers, topic):
        self.consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            auto_offset_reset='earliest',
            enable_auto_commit=True,
            group_id='chat-group',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )

    def consume_messages(self):
        for message in self.consumer:
            yield message.value

if __name__ == "__main__":
    consumer = KafkaMessageConsumer(['kafka:29092'], 'chat_messages')
    for message in consumer.consume_messages():
        print(f"Received: {message}")