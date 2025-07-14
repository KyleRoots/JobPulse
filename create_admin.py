#!/usr/bin/env python3
"""
Script to create an admin user for the XML Job Feed Portal
"""
from app import app, db, User
import sys
import getpass

def create_admin_user():
    """Create an admin user for the system"""
    with app.app_context():
        # Check if admin user already exists
        admin = User.query.filter_by(username='admin').first()
        if admin:
            print("Admin user already exists!")
            print(f"Username: {admin.username}")
            print(f"Email: {admin.email}")
            print(f"Created: {admin.created_at}")
            return
        
        # Create default admin user
        print("Creating default admin user...")
        
        # Default credentials
        username = 'admin'
        email = 'admin@myticas.com'
        password = 'MyticasXML2025!'
        
        # Check if email already exists
        if User.query.filter_by(email=email).first():
            print(f"User with email {email} already exists!")
            return
        
        # Create admin user
        admin = User(
            username=username,
            email=email,
            is_admin=True
        )
        admin.set_password(password)
        
        db.session.add(admin)
        db.session.commit()
        
        print(f"Admin user '{username}' created successfully!")
        print(f"Email: {email}")
        print(f"Password: {password}")
        print("You can now login to the XML Job Feed Portal.")

if __name__ == '__main__':
    create_admin_user()