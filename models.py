import uuid
from persistent import Persistent
from persistent.mapping import PersistentMapping
from ZODB import DB
from ZODB.FileStorage import FileStorage
import transaction
import base64
from typing import List, Dict
from persistent.list import PersistentList
from datetime import datetime

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
        self.deadline_polls = PersistentMapping()
        self.postit_notes = PersistentList() 
       
        self.create_default_chat()

    def create_default_chat(self):
        default_chats = ["general1", "announcements"]
        for chat_name in default_chats:
            existing = next((chat for chat in self.chats if chat.chat_id == chat_name), None)
            if existing:
                # 🔁 Make sure all current members are in the chat
                for member in self.members:
                    if member not in existing.participants:
                        existing.participants.append(member)
                        existing._p_changed = True
            else:
                # 🆕 Create new chat if it doesn't exist
                default_chat = Chat(chat_name, self.members)
                self.chats.append(default_chat)


   

    def add_postit_note(self, content, x, y, color=None):
        if not hasattr(self, "postit_notes"):
            self.postit_notes = PersistentList()

        if not color:
            import random
            color = random.choice(["#a1eafb", "#fffd82", "#ffa69e", "#b8f2e6", "#ff9f1c", "#c7ceea"])

        note = {
            "id": str(uuid.uuid4()),
            "content": content,
            "x": x,
            "y": y,
            "color": color
        }
        self.postit_notes.append(note)
        self._p_changed = True
        transaction.commit()
        return note



    def update_postit_position(self, note_id, x, y):
        for note in self.postit_notes:
            if note["id"] == note_id:
                note["x"] = x
                note["y"] = y
                self._p_changed = True
                transaction.commit()
                break

    def delete_postit_note(self, note_id):
        self.postit_notes = PersistentList(
            [note for note in self.postit_notes if note["id"] != note_id]
        )
        self._p_changed = True
        transaction.commit()

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

    def calculate_overall_progress(self):
        gantt_chart = getattr(self, "gantt_chart", [])
        normal_tasks = [
            t for t in gantt_chart
            if t.get("work_hours_per_day") and t.get("days")
        ]
        total_done = sum(int(t.get("completed_seconds", 0)) for t in normal_tasks)
        total_required = sum(int(t.get("hours_to_complete", 1)) * 3600 for t in normal_tasks)
        return round((total_done / total_required) * 100, 1) if total_required > 0 else 0

    def get_terminal_tasks(self):
        gantt_chart = getattr(self, "gantt_chart", [])
        referenced_preds = set()
        for t in gantt_chart:
            preds = t.get("predecessor", "")
            referenced_preds.update(p.strip() for p in preds.split(";") if p.strip())

        return [
            (i, t) for i, t in enumerate(gantt_chart)
            if str(i + 1) not in referenced_preds
        ]

    def calculate_status_and_days_remaining(self):
        gantt_chart = getattr(self, "gantt_chart", [])
        
        # If the Gantt chart is empty, set status to "Ongoing" by default
        if not gantt_chart:
            return "Ongoing", None
        
        terminal_tasks = self.get_terminal_tasks()
        today = datetime.today()

        all_terminal_completed = True
        latest_end_date = None

        for index, task in terminal_tasks:
            end = task.get("end_date")
            if end:
                try:
                    end_date = datetime.strptime(end, "%Y-%m-%d")
                    if not latest_end_date or end_date > latest_end_date:
                        latest_end_date = end_date
                except:
                    pass

            if task.get("work_hours_per_day") and task.get("days"):
                # Normal task
                completed = int(task.get("completed_seconds", 0))
                required = int(task.get("hours_to_complete", 1)) * 3600
                if completed < required:
                    all_terminal_completed = False
                    break
            else:
                # Milestone: check all its predecessors
                preds = task.get("predecessor", "").split(";")
                for pred_id in preds:
                    if not pred_id.isdigit():
                        continue
                    pred_index = int(pred_id) - 1
                    if 0 <= pred_index < len(gantt_chart):
                        pred_task = gantt_chart[pred_index]
                        if pred_task.get("work_hours_per_day") and pred_task.get("days"):
                            completed = int(pred_task.get("completed_seconds", 0))
                            required = int(pred_task.get("hours_to_complete", 1)) * 3600
                            if completed < required:
                                all_terminal_completed = False
                                break
                    if not all_terminal_completed:
                        break

        if all_terminal_completed:
            return "Completed", (latest_end_date - today).days if latest_end_date else None
        elif latest_end_date and latest_end_date < today:
            return "Overdue", (latest_end_date - today).days
        else:
            return "Ongoing", (latest_end_date - today).days if latest_end_date else None



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

                overall_progress = project.calculate_overall_progress()
                status, days_remaining = project.calculate_status_and_days_remaining()

                projects.append({
                    "name": project.name,
                    "code": project.code,
                    "members": members_info,
                    "overall_progress": overall_progress,
                    "status": status,
                    "days_remaining": days_remaining
                })

        return projects




    def add_user_to_project(self, username, project_code):
        """Add a user to a project."""
        user = self.get_user(username)
        project = self.get_project(project_code)

        if not user or not project:
            return "invalid_code"

        if username in project.members:
            return "already_member"

        project.members.append(username)
        project._p_changed = True
        user.projects.append(project_code)
        user._p_changed = True
        transaction.commit()
        return "success"

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

