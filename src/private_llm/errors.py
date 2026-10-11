class ServiceError(Exception):
    def __init__(self, status, code, message, retry_after=None):
        self.status, self.code, self.message = status, code, message
        self.retry_after = retry_after
        super().__init__(code)

    def body(self, request_id):
        return {'request_id': request_id, 'error': {'code': self.code, 'message': self.message}}
