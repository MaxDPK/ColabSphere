import uuid
from persistent import Persistent
from persistent.mapping import PersistentMapping
from ZODB import DB
from ZODB.FileStorage import FileStorage
import transaction
import base64
from typing import List, Dict
from persistent.list import PersistentList

# --------- USER CLASS ---------
class User(Persistent):
    def __init__(self, username, password, profile_pic=None):
        self.username = username
        self.password = password
        self.profile_pic_data = profile_pic  # Store binary data for profile picture
        self.projects = []  # List of project codes


# --------- CHAT CLASS ---------
class Chat(Persistent):
    def __init__(self, chat_id: str, participants: List[str]):
        self.chat_id = chat_id
        self.participants = participants  # List of usernames
        self.messages = []  # List of message dictionaries

    def add_message(self, user: str, message: str):
        """Adds a message to the chat and persists the change, ensuring no duplication."""
        clean_message = message.replace(f"{user}: ", "", 1) if message.startswith(f"{user}: ") else message
        self.messages.append({"user": user, "message": clean_message})
        self._p_changed = True  # Mark object as changed
        transaction.commit()  # Save changes

    def get_history(self):
        """Return chat history as a list of messages with correct keys."""
        # ✅ Convert old message format if needed
        corrected_history = []
        for msg in self.messages:
            if isinstance(msg, dict):
                if "sender" in msg and "content" in msg:
                    msg = {"user": msg["sender"], "message": msg["content"]}  # ✅ Convert old format
                corrected_history.append(msg)
            else:
                print(f"⚠️ WARNING: Skipping invalid message format: {msg}")

        return corrected_history  # ✅ Now all messages use "user" and "message"


# --------- PROJECT CLASS ---------
class Project(Persistent):
    def __init__(self, name, owner):
        self.name = name
        self.owner = owner
        self.code = str(uuid.uuid4())[:8]  # Generate a unique project code
        self.members = [owner]  # List of usernames
        self.gantt_chart = []  # List to store Gantt chart activities
        self.chats = []  # Store chat objects
        self.tasks = PersistentMapping()
       
        self.create_general_chat()

    def create_general_chat(self):
        """Ensure a default general chat exists."""
        if not any(chat.chat_id == "general1" for chat in self.chats):
            general_chat = Chat("general1", self.members)
            self.chats.append(general_chat)

    def get_chat(self, chat_id):
        """Retrieve a chat by ID."""
        for chat in self.chats:
            if chat.chat_id == chat_id:
                return chat
        return None

    def create_chat(self, chat_name: str, participants: List[str]):
        """
        Create a new chat with a user-defined name and selected participants.
        """
        if not participants:
            participants = self.members  # ✅ Default to all project members if none selected

        chat_id = chat_name
        chat = Chat(chat_id, participants)  # ✅ Assign selected participants
        self.chats.append(chat)
        self._p_changed = True
        transaction.commit()
        return chat

    def add_task(self, date, task, user):
        """Adds a task to the project for a specific date and user."""
        if date not in self.tasks:
            self.tasks[date] = []
        task_entry = {"id": str(uuid.uuid4()), "date": date, "task": task, "user": user}
        self.tasks[date].append(task_entry)
        self._p_changed = True
        transaction.commit()
        return task_entry

# --------- DATABASE CLASS ---------
class Database:
    def __init__(self, db_path="database/data.fs"):
        # Setup ZODB
        storage = FileStorage(db_path)
        self.db = DB(storage)
        self.connection = self.db.open()
        self.root = self.connection.root

        # Initialize root objects if they don't exist
        if not hasattr(self.root, "users"):
            self.root.users = PersistentMapping()
            transaction.commit()
        if not hasattr(self.root, "projects"):
            self.root.projects = PersistentMapping()
            transaction.commit()

    # --------- USER MANAGEMENT ---------
    def add_user(self, username, password):
        """Create a new user."""
        if username in self.root.users:
            return False
        self.root.users[username] = User(username, password)
        transaction.commit()
        return True

    def get_user(self, username):
        """Retrieve a user by username."""
        return self.root.users.get(username)

    def update_user_profile_pic(self, username, profile_pic_data):
        """Update the profile picture binary data for a user."""
        user = self.get_user(username)
        if user:
            user.profile_pic_data = profile_pic_data  # Store binary data
            user._p_changed = True
            transaction.commit()
            return True
        return False

    # --------- PROJECT MANAGEMENT ---------
    def create_project(self, name, owner):
        """Create a new project and assign it to the owner."""
        if owner not in self.root.users:
            return None
        project = Project(name, owner)
        self.root.projects[project.code] = project
        owner_obj = self.get_user(owner)
        owner_obj.projects.append(project.code)
        owner_obj._p_changed = True
        transaction.commit()
        return project.code

    def get_project(self, project_code):
        """Retrieve a project by its code."""
        return self.root.projects.get(project_code)

    def get_user_projects(self, username):
        """Retrieve all projects associated with a user."""
        user = self.get_user(username)
        if not user:
            return []

        projects = []
        for code in user.projects:
            project = self.get_project(code)
            if project:
                members_info = []
                for member in project.members:
                    member_obj = self.get_user(member)
                    if member_obj:
                        profile_pic = "/static/profile_pics/default.png"
                        if member_obj.profile_pic_data:
                            encoded_image = base64.b64encode(member_obj.profile_pic_data).decode("utf-8")
                            image_header = "image/png"
                            if member_obj.profile_pic_data[:2] == b'\xff\xd8':
                                image_header = "image/jpeg"
                            profile_pic = f"data:{image_header};base64,{encoded_image}"

                        members_info.append({"username": member, "profile_pic": profile_pic})

                projects.append({
                    "name": project.name,
                    "code": project.code,
                    "members": members_info
                })
        return projects

    def add_user_to_project(self, username, project_code):
        """Add a user to a project."""
        user = self.get_user(username)
        project = self.get_project(project_code)
        if not user or not project or username in project.members:
            return False
        project.members.append(username)
        project._p_changed = True
        user.projects.append(project_code)
        user._p_changed = True
        transaction.commit()
        return True

    # --------- CHAT MANAGEMENT ---------
    def get_chat(self, project_code, chat_id):
        """Retrieve a chat by project code and chat ID."""
        project = self.get_project(project_code)
        if project:
            return project.get_chat(chat_id)
        return None

    def create_chat(self, project_code, participants):
        """Create a new chat in a project."""
        project = self.get_project(project_code)
        if not project:
            return None
        return project.create_chat(participants)

    def save_gantt_chart(self, project_code, activities):
        """Save Gantt chart activities to a project."""
        project = self.get_project(project_code)
        if not project:
            return False, "Invalid project code"
        project.gantt_chart = activities
        project._p_changed = True
        transaction.commit()
        return True, "Gantt chart saved successfully"

    def close(self):
        """Close the database connection."""
        self.connection.close()
        self.db.close()


    

  


# Create a global instance of the database
db = Database()

