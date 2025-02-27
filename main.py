from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query, Form, Depends, Body, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from models import User, Project, db
from typing import Dict, List
import shutil
import os
from io import BytesIO
import base64
import json
from pydantic import BaseModel
import transaction


app = FastAPI()


# Templates and static files setup
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Track online users and WebSocket connections per project
online_users: Dict[str, set] = {}
project_connections: Dict[str, List[WebSocket]] = {}

@app.get("/", response_class=HTMLResponse)
async def home():
    return RedirectResponse("/login", status_code=303)




@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = db.get_user(username)
    if user:
        if user.password == password:
            # Set the user cookie after successful login
            response = RedirectResponse(f"/menu?user={username}", status_code=303)
            response.set_cookie(key="user", value=username)
            return response
        else:
            # Return to login with error message for incorrect password
            error_message = "Incorrect password. Please try again."
            return templates.TemplateResponse("login.html", {"request": request, "error_message": error_message})
    else:
        # Return to login with error message for non-existent username
        error_message = "Username does not exist. Please try again."
        return templates.TemplateResponse("login.html", {"request": request, "error_message": error_message})





@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "signup": True})

@app.post("/signup")
async def signup(request: Request, username: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    # Check if password and confirm password match
    if password != confirm_password:
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "signup": True,
            "error_message": "Passwords do not match. Please try again."
        })
    
    # Check if the user already exists
    if db.get_user(username):
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "signup": True,
            "error_message": "User already exists. Please try a different username."
        })
    
    # Proceed with adding the user if passwords match and username is available
    if db.add_user(username, password):
        response = RedirectResponse(f"/choose_profile_pic?user={username}", status_code=303)
        response.set_cookie(key="user", value=username)  # ✅ Set user cookie after signup
        return response
    
    return "An unexpected error occurred."



@app.get("/choose_profile_pic", response_class=HTMLResponse)
async def choose_profile_pic(request: Request, user: str):
    return templates.TemplateResponse("profpic.html", {"request": request, "user": user})

@app.post("/upload_profile_pic")
async def upload_profile_pic(user: str = Form(...), file: UploadFile = File(...)):
    """
    Uploads a user-selected profile picture.
    """
    user_obj = db.get_user(user)
    if not user_obj:
        return {"error": "User not found"}

    profile_pic_data = await file.read()  # Read file as binary
    db.update_user_profile_pic(user, profile_pic_data)

    return RedirectResponse(f"/menu?user={user}", status_code=303)

@app.post("/keep_default_profile_pic")
async def keep_default_profile_pic(user: str = Form(...)):
    """
    Resets the user's profile picture to the default image.
    """
    user_obj = db.get_user(user)
    if not user_obj:
        return {"error": "User not found"}

    # Set the profile picture to default (empty or predefined binary default)
    db.update_user_profile_pic(user, None)

    return RedirectResponse(f"/menu?user={user}", status_code=303)

@app.get("/menu", response_class=HTMLResponse)
async def menu(request: Request, user: str):
    user_data = db.get_user(user)
    if not user_data:
        return RedirectResponse("/login", status_code=303)

    projects = db.get_user_projects(user_data.username)
    profile_pic = "/static/profile_pics/default.png"  # Default image

    if user_data.profile_pic_data:
        try:
            # Convert binary image data to Base64
            encoded_image = base64.b64encode(user_data.profile_pic_data).decode('utf-8')

            # Determine MIME type (PNG, JPEG, etc.)
            image_header = "image/png"  # Default type
            if user_data.profile_pic_data[:2] == b'\xff\xd8':  # JPEG magic bytes
                image_header = "image/jpeg"

            profile_pic = f"data:{image_header};base64,{encoded_image}"
            
            # Debugging
            print(f"Final Base64 image string: {profile_pic[:100]}...")

        except Exception as e:
            print("Error converting profile picture:", e)

    return templates.TemplateResponse(
        "menu.html",
        {"request": request, "projects": projects, "user": user, "profile_pic": profile_pic}
    )






@app.post("/create_project")
async def create_project(request: Request, project_name: str = Form(...)):
    user = request.cookies.get("user")  # Get the user from cookies
    if not user:
        return RedirectResponse("/login", status_code=303)  # Redirect to login if no user

    project_code = db.create_project(project_name, user)  # Create the project
    # Redirect to /menu with the user query parameter
    return RedirectResponse(f"/menu?user={user}", status_code=303)




@app.post("/join_project")
async def join_project(request: Request, project_code: str = Form(...)):
    user = request.cookies.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    success = db.add_user_to_project(user, project_code)
    if success:
        return RedirectResponse(f"/menu?user={user}", status_code=303)
    else:
        return "Invalid project code or already a member"

@app.get("/project_hub/{project_code}", response_class=HTMLResponse)
async def project_hub(request: Request, project_code: str, user: str):
    project = db.get_project(project_code)

    if not project:
        return "Invalid project"
    
    if user not in project.members:
        return "You are not a member of this project"
    
    if not any(chat.chat_id == "general1" for chat in project.chats):
        project.create_general_chat()

    
    return templates.TemplateResponse("project_hub.html", {
        "request": request,
        "project": project,
        "members": project.members,
        "user": user
    })



@app.websocket("/ws/{project_code}")
async def websocket_endpoint(websocket: WebSocket, project_code: str, user: str = Query(...)):
    await websocket.accept()

    # ✅ Ensure the project is in the dictionaries
    if project_code not in project_connections:
        project_connections[project_code] = []
    if project_code not in online_users:
        online_users[project_code] = set()

    # ✅ Add WebSocket & user
    project_connections[project_code].append(websocket)
    online_users[project_code].add(user)

    print(f"🟢 {user} is now ONLINE in project {project_code}")

    # ✅ Notify all clients about updated online users
    await notify_project_users(project_code)

    try:
        while True:
            await websocket.receive_text()  # Keep connection open
    except WebSocketDisconnect:
        print(f"🔴 {user} went OFFLINE in project {project_code}")

        # ✅ SAFELY REMOVE WebSocket if it exists
        if websocket in project_connections.get(project_code, []):
            project_connections[project_code].remove(websocket)

        # ✅ SAFELY REMOVE User if they exist
        if user in online_users.get(project_code, set()):
            online_users[project_code].remove(user)

        # ✅ REMOVE EMPTY PROJECT LISTS to clean memory
        if not project_connections[project_code]:
            del project_connections[project_code]
        if not online_users[project_code]:
            del online_users[project_code]

        # ✅ Notify remaining clients about updated online users
        await notify_project_users(project_code)



async def notify_project_users(project_code: str):
    """Send the updated online users list to all WebSocket clients in the project."""
    if project_code in project_connections:
        for connection in project_connections[project_code]:
            try:
                await connection.send_json({
                    "action": "update",
                    "online_users": list(online_users[project_code])
                })
            except Exception:
                # If a WebSocket connection is closed unexpectedly, remove it
                project_connections[project_code].remove(connection)


@app.get("/gantt_chart", response_class=HTMLResponse)
async def gantt_chart(request: Request, project_code: str, user: str):
    project = db.get_project(project_code)
    if not project:
        return "Invalid project"
    if user not in project.members:
        return "You are not a member of this project"
    return templates.TemplateResponse("gantt_chart.html", {
        "request": request,
        "project_code": project_code,
        "user": user,
        "members": project.members,  # Pass the list of team members
        "activities": getattr(project, "gantt_chart", [])  # Safeguard if gantt_chart doesn't exist yet
    })



@app.get("/gantt_chart", response_class=HTMLResponse)
async def gantt_chart(request: Request, project_code: str, user: str):
    project = db.get_project(project_code)
    if not project:
        return "Invalid project"
    if user not in project.members:
        return "You are not a member of this project"
    
    activities = getattr(project, "gantt_chart", [])  # Retrieve Gantt chart activities
    return templates.TemplateResponse("gantt_chart.html", {
        "request": request,
        "project_code": project_code,
        "user": user,
        "members": project.members,
        "activities": activities  # Pass activities to the template
    })

@app.get("/get_activities")
async def get_activities(project_code: str):
    project = db.get_project(project_code)
    if not project:
        return {"activities": []}  # Return an empty list if the project is invalid
    return {"activities": getattr(project, "gantt_chart", [])}




@app.post("/save_gantt_chart")
async def save_gantt_chart(data: dict = Body(...)):
    print(data)
    project_code = data.get("project_code")
    activities = data.get("activities", [])

    # Call the database method to save the Gantt chart
    success, message = db.save_gantt_chart(project_code, activities)

    if not success:
        return {"message": message}  # Return error message if saving failed

    return {"message": message}  # Return success message






@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("user")
    return response




WHITEBOARD_DIR = "whiteboards"
os.makedirs(WHITEBOARD_DIR, exist_ok=True)

def get_whiteboard_path(project_code: str) -> str:
    """Return the file path for a project's whiteboard."""
    return os.path.join(WHITEBOARD_DIR, f"{project_code}_whiteboard.json")

def load_project_whiteboard(project_code: str) -> List[Dict]:
    try:
        with open(get_whiteboard_path(project_code), "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return []

def save_project_whiteboard(project_code: str, data: List[Dict]):
    with open(get_whiteboard_path(project_code), "w") as file:
        json.dump(data, file)

@app.get("/project_whiteboard/{project_code}", response_class=HTMLResponse)
async def project_whiteboard(request: Request, project_code: str, user: str):
    """Serve the whiteboard page for a specific project."""
    return templates.TemplateResponse("white_board.html", {
        "request": request,
        "project_code": project_code,
        "user": user
    })

@app.websocket("/ws/whiteboard/{project_code}")
async def websocket_whiteboard(websocket: WebSocket, project_code: str):
    await websocket.accept()
    if project_code not in project_connections:
        project_connections[project_code] = []
    project_connections[project_code].append(websocket)

    # Load existing whiteboard data
    drawing_history = load_project_whiteboard(project_code)
    for stroke in drawing_history:
        await websocket.send_text(json.dumps(stroke))

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message.get("action") == "clear":
                drawing_history.clear()
                save_project_whiteboard(project_code, drawing_history)
            else:
                drawing_history.append(message)
                save_project_whiteboard(project_code, drawing_history)

            # Broadcast update to all clients
            for conn in project_connections[project_code]:
                await conn.send_text(data)

    except WebSocketDisconnect:
        project_connections[project_code].remove(websocket)




@app.get("/chat/{chat_id}", response_class=HTMLResponse)
async def chat_page(request: Request, chat_id: str, user: str, project_code: str = Query(None)):
    """
    Serve chat UI ensuring only authorized members can access.
    """
    if not project_code:
        raise HTTPException(status_code=400, detail="Project code is missing.")

    project = db.get_project(project_code)
    if not project:
        return "Error: Project not found"

    chat = project.get_chat(chat_id)
    if not chat:
        return "Error: Chat not found"

    # ✅ Restrict access to only chat participants
    if user not in chat.participants:
        return "Error: You are not a participant in this chat"

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "chat": chat,
        "user": user,
        "project_code": project_code
    })


@app.websocket("/ws/chat/{chat_id}")
async def chat_websocket(websocket: WebSocket, chat_id: str):
    """Handle WebSocket chat messages and send chat history on connection."""
    
    await websocket.accept()
    
    query_params = websocket.query_params
    user = query_params.get("user")
    project_code = query_params.get("project_code")

    print(f"✅ WebSocket connected: chat_id={chat_id}, user={user}, project_code={project_code}")

    if not project_code:
        print("❌ ERROR: Project code is missing, closing WebSocket.")
        await websocket.send_json({"error": "Project code is missing"})
        await websocket.close()
        return

    # ✅ Fetch project and chat
    project = db.get_project(project_code)
    if not project:
        print(f"❌ ERROR: Project {project_code} not found. Closing WebSocket.")
        await websocket.send_json({"error": "Project not found"})
        await websocket.close()
        return

    chat = project.get_chat(chat_id)
    if not chat:
        print(f"❌ ERROR: Chat {chat_id} not found in project {project_code}. Closing WebSocket.")
        await websocket.send_json({"error": "Chat not found"})
        await websocket.close()
        return

    # ✅ Ensure user is a participant
    if user not in chat.participants:
        print(f"❌ ERROR: User {user} is not a participant in chat {chat_id}. Closing WebSocket.")
        await websocket.send_json({"error": "You are not a participant in this chat"})
        await websocket.close()
        return

    print(f"🎉 SUCCESS: WebSocket connected for chat {chat_id}")

    # ✅ Convert old messages if necessary and send chat history
    chat_history = chat.get_history()
    await websocket.send_json({"action": "history", "messages": chat_history})

    # ✅ Track connected users
    if chat_id not in project_connections:
        project_connections[chat_id] = []
    project_connections[chat_id].append(websocket)

    try:
        while True:
            message = await websocket.receive_text()
            print(f"📩 Received message: {message}")

            # ✅ Store the message in chat history
            chat.add_message(user, message)

            # ✅ Broadcast message to all clients in the chat
            for conn in project_connections[chat_id]:
                clean_message = message.replace(f"{user}: ", "", 1) if message.startswith(f"{user}: ") else message

                await conn.send_json({"action": "new_message", "user": user, "message": clean_message})

    except WebSocketDisconnect:
        print(f"❌ WebSocket disconnected for {user} in chat {chat_id}")
        project_connections[chat_id].remove(websocket)


# Pydantic model for request validation
class ChannelRequest(BaseModel):
    project_code: str
    channel_name: str
    members: List[str] 

@app.post("/add_channel")
async def add_channel(request: ChannelRequest):
    """
    Adds a new channel to the project with selected members.
    """
    project = db.get_project(request.project_code)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # ✅ Check if the channel already exists
    if request.channel_name in [chat.chat_id for chat in project.chats]:
        return {"message": "Channel already exists"}

    # ✅ Ensure at least one participant is selected
    if not request.members:
        raise HTTPException(status_code=400, detail="At least one member must be selected.")

    # ✅ Create chat with selected members
    new_chat = project.create_chat(request.channel_name, participants=request.members)

    return {"message": "Channel added successfully", "chat_id": new_chat.chat_id}



@app.get("/get_channels")
async def get_channels(project_code: str, user: str):
    """
    Retrieves only the channels where the user is a participant.
    """
    project = db.get_project(project_code)
    if not project:
        return {"channels": []}  # Return an empty list if project not found

    # ✅ Return only chats where the user is a participant
    user_chats = [chat.chat_id for chat in project.chats if user in chat.participants]

    return {"channels": user_chats}


@app.post("/rename_chat")
async def rename_chat(data: dict = Body(...)):
    """
    Renames an existing chat for all members in a project.
    """
    project = db.get_project(data["project_code"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chat = project.get_chat(data["chat_id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    chat.chat_id = data["new_name"]
    chat._p_changed = True
    transaction.commit()

    return {"message": "Chat renamed successfully"}

@app.get("/get_chat_members")
async def get_chat_members(project_code: str, chat_id: str):
    """
    Fetches all project members and identifies those already in the selected chat.
    """
    project = db.get_project(project_code)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chat = project.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Get all project members
    all_members = project.members  # Fetch all members in the project

    # Get current chat members
    chat_members = set(chat.participants)  # Convert to set for fast lookup

    # Format response
    member_list = [{"username": member, "is_member": member in chat_members} for member in all_members]

    return JSONResponse(content={"members": member_list})

@app.post("/update_chat_members")
async def update_chat_members(data: dict = Body(...)):
    """
    Updates the members of an existing chat, allowing users to be added or removed.
    """
    project = db.get_project(data["project_code"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chat = project.get_chat(data["chat_id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    chat.participants = data["members"]
    chat._p_changed = True
    transaction.commit()

    return {"message": "Chat members updated successfully"}


