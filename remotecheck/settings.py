import os

class Settings:
    nameserver = None

    def __init__(self):
        self.reinit()
    
    def reinit(self):
        self.nameservers = None
        if os.getenv("SENSOR_NAMESERVER"):
            self.nameserver = os.getenv("SENSOR_NAMESERVER")

settings = Settings()
