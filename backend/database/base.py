import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import declarative_base

table_class = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

def utc_now():
    return datetime.now(timezone.utc)
