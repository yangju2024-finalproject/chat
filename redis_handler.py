import redis

class RedisHandler:
    def __init__(self, host='localhost', port=6379, db=0):
        self.redis_client = redis.Redis(host=host, port=port, db=db)

    def increment_message_count(self):
        return self.redis_client.incr('message_count')

    def get_message_count(self):
        return int(self.redis_client.get('message_count') or 0)