from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import UserCreate

router = APIRouter()

@router.get("/")
def root():
    return {
        "message": "Home Platform API is running!"
    }

@router.get("/db")
def database_test(db: Session = Depends(get_db)):
    version = db.scalar(text("SELECT version()"))

    return {
        "connected": True,
        "version": version,
    }


@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id)).all()

    return {
        "users": [
            {"id": user.id, "email": user.email, "name": user.name}
            for user in users
        ]
    }

@router.get("/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)

    if not user:
        return {
            "message": "User not found",
            "user_id": user_id,
        }

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
    }

@router.post("/users")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    new_user = User(email=user.email, name=user.name)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "message": "User created successfully",
        "id": new_user.id,
        "email": new_user.email,
        "name": new_user.name,
    }

@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)

    if not user:
        return {
            "message": "User not found",
            "user_id": user_id,
        }

    db.delete(user)
    db.commit()

    return {
        "message": "User deleted successfully",
        "user_id": user_id,
    }

@router.put("/users/{user_id}")
def update_user(user_id: int, user: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.get(User, user_id)

    if not existing_user:
        return {
            "message": "User not found",
            "user_id": user_id,
        }

    existing_user.email = user.email
    existing_user.name = user.name
    db.commit()
    db.refresh(existing_user)

    return {
        "message": "User updated successfully",
        "id": existing_user.id,
        "email": existing_user.email,
        "name": existing_user.name,
    }