import os

class Settings:
    nameserver = None

    def __init__(self):
        self.reinit()
    
    def reinit(self):
        self.nameserver = os.getenv("SENSOR_NAMESERVER")        
        self.checkfilter = os.getenv("SENSOR_CHECKFILTER", "")

settings = Settings()
