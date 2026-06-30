# models/user.py
from sqlalchemy import Column, Integer, String
from utils.database import Base, engine

class UserModel(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)

# NEW: This line actually physically creates the table in codegraph_users.db
Base.metadata.create_all(bind=engine)