class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status

    def as_dict(self):
        return {"error": self.code, "message": str(self)}
