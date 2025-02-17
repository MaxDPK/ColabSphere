import uuid
from persistent import Persistent
from persistent.mapping import PersistentMapping
from ZODB import DB
from ZODB.FileStorage import FileStorage
import transaction
import base64

class User(Persistent):
    def __init__(self, username, password, profile_pic=None):
        self.username = username
        self.password = password
        self.profile_pic_data = profile_pic  # Store binary data for profile picture
        self.projects = []  # List of project codes

        


class Project(Persistent):
    def __init__(self, name, owner):
        self.name = name
        self.owner = owner
        self.code = str(uuid.uuid4())[:8]  # Generate a unique project code
        self.members = [owner]  # List of usernames
        self.gantt_chart = []  # List to store Gantt chart activities



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
            transaction.commit()  # Commit initial state
        if not hasattr(self.root, "projects"):
            self.root.projects = PersistentMapping()
            transaction.commit()  # Commit initial state

    def add_user(self, username, password):
        if username in self.root.users:
            return False
        self.root.users[username] = User(username, password)
        transaction.commit()  # Persist the new user
        return True

    def get_user(self, username):
        user = self.root.users.get(username)
        if user:
            # Ensure `profile_pic_data` exists for old users
            if not hasattr(user, "profile_pic_data"):  
                user.profile_pic_data = None  # Default to None
                user._p_changed = True  # Mark the object as changed
                transaction.commit()
        return user


    
    def update_user_profile_pic(self, username, profile_pic_data):
        """Update the profile picture binary data for a user."""
        user = self.get_user(username)
        if user:
            user.profile_pic_data = profile_pic_data  # Store binary data
            user._p_changed = True  # Mark the user object as changed
            transaction.commit()  # Save changes to the database
            return True
        return False


    def create_project(self, name, owner):
        if owner not in self.root.users:
            return None
        project = Project(name, owner)
        self.root.projects[project.code] = project
        owner_obj = self.root.users[owner]
        owner_obj.projects.append(project.code)  # Add project to user's project list
        owner_obj._p_changed = True  # Mark the user object as changed
        transaction.commit()  # Persist the new project and user changes
        return project.code

    def get_project(self, code):
        return self.root.projects.get(code)

    def get_user_projects(self, username):
        user = self.get_user(username)
        if not user:
            return []
        
        projects = []
        for code in user.projects:
            project = self.get_project(code)
            if project:
                # Get members and their profile pictures
                members_info = []
                for member in project.members:
                    member_obj = self.get_user(member)
                    if member_obj:
                        profile_pic = "/static/profile_pics/default.png"  # Default profile pic
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
        user = self.get_user(username)
        project = self.get_project(project_code)
        if not user or not project:
            return False
        if username in project.members:
            return False  # User is already a member
        project.members.append(username)
        project._p_changed = True  # Mark the project object as changed
        user.projects.append(project_code)
        user._p_changed = True  # Mark the user object as changed
        transaction.commit()  # Persist the changes
        return True
    
    def save_gantt_chart(self, project_code, activities):
        """Save Gantt chart activities to a specific project."""
        project = self.get_project(project_code)
        if not project:
            return False, "Invalid project code"

        # Update the Gantt chart
        project.gantt_chart = activities
        project._p_changed = True  # Mark the project as changed
        transaction.commit()  # Persist the changes
        return True, "Gantt chart saved successfully"


    def close(self):
        self.connection.close()
        self.db.close()


# Create a global instance of the database
db = Database()
