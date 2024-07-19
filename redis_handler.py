import redis

class RedisHandler:
    def __init__(self, host='localhost', port=6379, db=0):
        self.redis_client = redis.Redis(host=host, port=port, db=db)

    def increment_message_count(self):
        return self.redis_client.incr('message_count')

    def get_message_count(self):
        count = self.redis_client.get('message_count')
        return int(count) if count else 0

    def reset_message_count(self):
        self.redis_client.set('message_count', 0)