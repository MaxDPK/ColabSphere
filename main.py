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
import uuid
import datetime
from pathlib import Path

app = FastAPI()

# Create uploads directory if it doesn't exist
UPLOAD_DIR = Path("static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Templates and static files setup
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Track online users and WebSocket connections per project
online_users: Dict[str, set] = {}
project_connections: Dict[str, List[WebSocket]] = {}
task_connections: Dict[str, List[WebSocket]] = {}
recent_projects_store = {}
working_tasks: Dict[str, Dict[str, dict]] = {}  



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
    if password != confirm_password:
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "error_message": "Passwords do not match. Please try again.",
            "username": username  # Preserve the username input
        })

    if db.get_user(username):
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "error_message": "Username already exists. Please choose a different username.",
            "username": username  # Preserve the username input
        })

    if db.add_user(username, password):
        return RedirectResponse(f"/choose_profile_pic?user={username}", status_code=303)

    return templates.TemplateResponse("signup.html", {
        "request": request,
        "error_message": "An unexpected error occurred. Please try again.",
        "username": username  # Preserve the username input
    })






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

    return RedirectResponse("/login", status_code=303)

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

    return RedirectResponse("/login", status_code=303)

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


    recent_projects = recent_projects_store.get(user, [])[:3]
     # ✅ Fix: Set `project` as an empty dictionary if no projects exist
    project = projects[0] if projects else {"code": ""}

    return templates.TemplateResponse(
        "menu.html",
        {"request": request, "projects": projects, "user": user, "profile_pic": profile_pic, "recent_projects": recent_projects, "project": project}
    )







@app.post("/create_project")
async def create_project(
    request: Request,
    project_name: str = Form(...),
    user: str = Form(...)
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    project_code = db.create_project(project_name, user)
    return RedirectResponse(f"/menu?user={user}", status_code=303)

@app.post("/join_project", response_class=HTMLResponse)
async def join_project(
    request: Request,
    project_code: str = Form(...),
    user: str = Form(...)
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    # Must return "success", "already_member", or "invalid_code"
    result = db.add_user_to_project(user, project_code)

    if result == "success":
        return RedirectResponse(f"/menu?user={user}", status_code=303)
    elif result == "already_member":
        error_message = "You're already a member of this project!"
    else:
        error_message = "Invalid project code. Please check and try again."

    # Load UI with error message
    user_data = db.get_user(user)
    projects = db.get_user_projects(user_data.username)
    profile_pic = "/static/profile_pics/default.png"

    if user_data.profile_pic_data:
        try:
            encoded_image = base64.b64encode(user_data.profile_pic_data).decode('utf-8')
            image_header = "image/png"
            if user_data.profile_pic_data[:2] == b'\xff\xd8':
                image_header = "image/jpeg"
            profile_pic = f"data:{image_header};base64,{encoded_image}"
        except Exception as e:
            print("Error converting profile picture:", e)

    recent_projects = recent_projects_store.get(user, [])[:3]
    return templates.TemplateResponse("menu.html", {
        "request": request,
        "projects": projects,
        "user": user,
        "profile_pic": profile_pic,
        "recent_projects": recent_projects,
        "error": error_message,
        "show_join_modal": True
    })

@app.get("/project_hub/{project_code}", response_class=HTMLResponse)
async def project_hub(request: Request, project_code: str, user: str):
    project = db.get_project(project_code)

    if not project:
        return "Invalid project"
    
    if user not in project.members:
        return "You are not a member of this project"
    
    if not getattr(project, "gantt_chart", []):
        print("⚠️ Warning: project.gantt_chart is empty. Reload from disk if needed.")


    all_tasks = [
        {
            **task, 
            "completed_seconds": int(task.get("completed_seconds", 0)),  
            "hours_to_complete": int(task.get("hours_to_complete", 1))
        }
        for task in getattr(project, "gantt_chart", [])
    ]

    assigned_tasks = [
        task for task in all_tasks if isinstance(task.get("assigned_to"), list) and user in task["assigned_to"]
    ]

    total_done = sum(task["completed_seconds"] for task in all_tasks)
    total_required = sum(task["hours_to_complete"] * 3600 for task in all_tasks)
    overall_progress = round((total_done / total_required) * 100, 1) if total_required > 0 else 0
    
    if not any(chat.chat_id == "general1" for chat in project.chats):
        project.create_general_chat()


    # ✅ Update the recent projects list for the user
    if user not in recent_projects_store:
        recent_projects_store[user] = []
    
    # Remove existing entry if project is already in the list
    recent_projects_store[user] = [p for p in recent_projects_store[user] if p["code"] != project_code]

    # Add the project to the top of the recent list
    recent_projects_store[user].insert(0, {"name": project.name, "code": project.code})

    # Keep only the last 3 projects
    recent_projects_store[user] = recent_projects_store[user][:3]
    
    return templates.TemplateResponse("project_hub.html", {
        "request": request,
        "project": project,
        "members": project.members,
        "user": user,
        "assigned_tasks": assigned_tasks,
        "all_tasks": all_tasks,
        "overall_progress": overall_progress
    })

@app.get("/get_recent_projects")
async def get_recent_projects(user: str):
    """Returns the user's recent projects dynamically."""
    recent_projects = recent_projects_store.get(user, [])[:3]
    return JSONResponse(content={"recent_projects": recent_projects})




active_users_per_task: Dict[str, int] = {}  # Track active workers per task


@app.websocket("/ws/{project_code}")
async def websocket_endpoint(websocket: WebSocket, project_code: str, user: str = Query(...)):
    await websocket.accept()

    if project_code not in project_connections:
        project_connections[project_code] = []
    if project_code not in online_users:
        online_users[project_code] = set()
    if project_code not in working_tasks:
        working_tasks[project_code] = {}

    project_connections[project_code].append(websocket)
    online_users[project_code].add(user)

    await notify_project_users(project_code)

    try:
        while True:
            data = await websocket.receive_json()

            action = data.get("action")
            task_name = data.get("task_name")
            assigned_to = data.get("assigned_to")
            task_key = f"{task_name}-{','.join(assigned_to)}"

            if action == "start_work":
                active_users_per_task[task_key] = active_users_per_task.get(task_key, 0) + 1
                print(f"🟢 {active_users_per_task[task_key]} users working on {task_name}")

                # ✅ Track in working_tasks
                working_tasks[project_code][user] = {
                    "task_name": task_name,
                    "assigned_to": assigned_to
                }

                await broadcast_active_users(project_code, task_name, assigned_to)

                start_msg = {
                    "action": "start_work",
                    "task_name": task_name,
                    "assigned_to": assigned_to,
                    "username": user,
                    "working_on_task": task_name,
                    "online_users": await get_online_user_dict(project_code)
                }

                for connection in project_connections[project_code]:
                    await connection.send_json(start_msg)

            elif action == "stop_work":
                if task_key in active_users_per_task:
                    active_users_per_task[task_key] -= 1
                    if active_users_per_task[task_key] <= 0:
                        del active_users_per_task[task_key]

                print(f"🔴 {active_users_per_task.get(task_key, 0)} users working on {task_name}")

                # ✅ Remove from working_tasks
                if user in working_tasks[project_code]:
                    del working_tasks[project_code][user]

                await broadcast_active_users(project_code, task_name, assigned_to)

                stop_msg = {
                    "action": "stop_work",
                    "task_name": task_name,
                    "assigned_to": assigned_to,
                    "username": user,
                    "final_completed_seconds": data.get("final_completed_seconds", 0),
                    "working_on_task": None,
                    "online_users": await get_online_user_dict(project_code)
                }

                for connection in project_connections[project_code]:
                    await connection.send_json(stop_msg)

    except WebSocketDisconnect:
        print(f"🔴 {user} went OFFLINE in project {project_code}")

        if websocket in project_connections.get(project_code, []):
            project_connections[project_code].remove(websocket)

        if user in online_users.get(project_code, set()):
            online_users[project_code].remove(user)

        # ✅ Remove user’s working state too
        if project_code in working_tasks and user in working_tasks[project_code]:
            del working_tasks[project_code][user]

        if not project_connections[project_code]:
            del project_connections[project_code]
        if not online_users[project_code]:
            del online_users[project_code]
        if not working_tasks[project_code]:
            del working_tasks[project_code]

        await notify_project_users(project_code)



async def broadcast_active_users(project_code: str, task_name: str, assigned_to: list):
    """Send active worker count update to all WebSocket clients"""
    task_key = f"{task_name}-{','.join(assigned_to)}"
    active_workers = active_users_per_task.get(task_key, 0)

    update_message = {
        "action": "update_active_users",
        "task_name": task_name,
        "assigned_to": assigned_to,
        "active_workers": active_workers
    }

    if project_code in project_connections:
        for connection in project_connections[project_code]:
            await connection.send_json(update_message)

@app.get("/get_working_tasks")
async def get_working_tasks(project_code: str):
    return working_tasks.get(project_code, {})




@app.websocket("/ws/tasks/{project_code}")
async def websocket_task_endpoint(websocket: WebSocket, project_code: str):
    """Handles WebSocket connections for real-time task updates."""
    await websocket.accept()

    if project_code not in task_connections:
        task_connections[project_code] = []
    task_connections[project_code].append(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        task_connections[project_code].remove(websocket)
        if not task_connections[project_code]:
            del task_connections[project_code]


async def notify_project_users(project_code: str):
    """Send updated online users with profile pictures to WebSocket clients."""
    if project_code in project_connections:
        online_users_with_pics = {}

        for user in online_users.get(project_code, []):
            user_data = db.get_user(user)
            profile_pic = "/static/profile_pics/default.png"  # Default picture

            if user_data and user_data.profile_pic_data:
                try:
                    encoded_image = base64.b64encode(user_data.profile_pic_data).decode("utf-8")
                    image_header = "image/png"
                    if user_data.profile_pic_data[:2] == b'\xff\xd8':  # JPEG check
                        image_header = "image/jpeg"
                    profile_pic = f"data:{image_header};base64,{encoded_image}"
                except Exception as e:
                    print(f"Error encoding profile picture for {user}: {e}")

            online_users_with_pics[user] = profile_pic

        for connection in project_connections[project_code]:
            try:
                await connection.send_json({
                    "action": "update",
                    "online_users": online_users_with_pics
                })
            except Exception:
                project_connections[project_code].remove(connection)


async def get_online_user_dict(project_code: str) -> Dict[str, str]:
    """
    Returns a dictionary of {username: profile_pic_data_url} for online users in a project.
    """
    user_map = {}
    for username in online_users.get(project_code, []):
        user_data = db.get_user(username)
        profile_pic = "/static/profile_pics/default.png"

        if user_data and user_data.profile_pic_data:
            try:
                encoded_image = base64.b64encode(user_data.profile_pic_data).decode("utf-8")
                image_header = "image/png"
                if user_data.profile_pic_data[:2] == b'\xff\xd8':
                    image_header = "image/jpeg"
                profile_pic = f"data:{image_header};base64,{encoded_image}"
            except Exception as e:
                print(f"Error encoding profile pic for {username}: {e}")

        user_map[username] = profile_pic

    return user_map



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
        return {"activities": []}

    activities = getattr(project, "gantt_chart", [])

    for activity in activities:
        # ✅ Make sure `assigned_to` stays a list!
        if isinstance(activity.get("assigned_to"), str):
            activity["assigned_to"] = [u.strip() for u in activity["assigned_to"].split(";") if u.strip()]

        # ✅ Ensure `completed_seconds` is always present
        if "work_hours_per_day" in activity and "days" in activity:
            if "completed_seconds" not in activity:
                activity["completed_seconds"] = 0  # Default to 0

    return {"activities": activities}







@app.post("/save_gantt_chart")
async def save_gantt_chart(data: dict = Body(...)):
    project_code = data.get("project_code")
    activities = data.get("activities", [])

    # Make sure project exists
    project = db.get_project(project_code)
    if not project:
        return JSONResponse(content={"error": "Project not found"}, status_code=404)

    for activity in activities:
        if isinstance(activity.get("assigned_to"), str):
            assigned_users = activity["assigned_to"].strip()
            activity["assigned_to"] = assigned_users.split(";") if assigned_users else []

        if "work_hours_per_day" in activity and "days" in activity:
            if "completed_seconds" not in activity:
                activity["completed_seconds"] = 0

    success, message = db.save_gantt_chart(project_code, activities)

    if success:
        # Notify Gantt clients
        if project_code in project_connections:
            for connection in project_connections[project_code]:
                await connection.send_json({
                    "action": "gantt_chart_update",
                    "activities": activities
                })

        # Push deadlines to calendar
        if not hasattr(project, "tasks"):
            project.tasks = {}

        for activity in activities:
            if "end_date" in activity:
                assigned_users = []
                for username in activity.get("assigned_to", []):
                    user = db.get_user(username)
                    if user:
                        if user.profile_pic_data:
                            encoded = base64.b64encode(user.profile_pic_data).decode()
                            mime_type = "image/png" if user.profile_pic_data[:2] != b'\xff\xd8' else "image/jpeg"
                            profile_pic = f"data:{mime_type};base64,{encoded}"
                        else:
                            profile_pic = "/static/profile_pics/default.png"

                        assigned_users.append({
                            "username": user.username,
                            "profile_pic": profile_pic
                        })

                calendar_task = {
                    "id": f"gantt-{activity['row_num']}",
                    "date": activity["end_date"],
                    "description": activity["name"],
                    "user": "system",
                    "color": "#fbc02d",
                    "label": "Deadline",
                    "time": None,
                    "is_all_day": True,
                    "assigned_to": assigned_users,
                    "start_date": activity.get("start_date", ""),
                    "predecessor": activity.get("predecessor", ""),
                    "completed_seconds": activity.get("completed_seconds", 0),
                    "hours_to_complete": int(activity.get("hours_to_complete", 1))
                }



                if activity["end_date"] not in project.tasks:
                    project.tasks[activity["end_date"]] = []

                project.tasks[activity["end_date"]] = [
                    t for t in project.tasks[activity["end_date"]]
                    if not t["id"].startswith("gantt-") or t["id"] != calendar_task["id"]
                ]

                project.tasks[activity["end_date"]].append(calendar_task)

                # Notify calendar clients
                if project_code in task_connections:
                    for connection in task_connections[project_code]:
                        await connection.send_json({
                            "action": "task_update",
                            "task": calendar_task
                        })

        project._p_changed = True
        transaction.commit()

    return {"message": message}


@app.post("/update_task_progress")
async def update_task_progress(data: dict = Body(...)):
    import base64

    project_code = data.get("project_code")
    task_name = data.get("task_name")
    assigned_to = data.get("assigned_to")
    completed_seconds = int(data.get("completed_seconds", 0))

    print(f"🔹 Received update request: project_code={project_code}, task_name={task_name}, assigned_to={assigned_to}, completed_seconds={completed_seconds}")

    if not project_code:
        return {"success": False, "message": "Missing project_code"}

    project = db.get_project(project_code)
    if not project:
        return {"success": False, "message": "Invalid project"}

    updated = False
    for task in project.gantt_chart:
        if task.get("name") == task_name and sorted(task.get("assigned_to", [])) == sorted(assigned_to):
            prev_seconds = task.get("completed_seconds", 0)

            if completed_seconds > prev_seconds:
                task["completed_seconds"] = completed_seconds
                updated = True
                print(f"✅ Task match found: Updating completed_time from {prev_seconds}s to {completed_seconds}s")

    if updated and completed_seconds > prev_seconds:
        success, message = db.save_gantt_chart(project_code, project.gantt_chart)

        # ✅ Send to Gantt viewers (progress bar UI)
        if success and project_code in project_connections:
            update_message = {
                "action": "update_progress",
                "task_name": task_name,
                "assigned_to": assigned_to,
                "completed_seconds": completed_seconds,
                "formatted_time": f"{completed_seconds // 3600}h {(completed_seconds % 3600) // 60}m {completed_seconds % 60}s"
            }
            print(f"📡 Sending WebSocket update to Gantt view: {update_message}")

            for connection in project_connections[project_code]:
                await connection.send_json(update_message)

        # ✅ Also update the Gantt calendar task
        if not hasattr(project, "tasks"):
            project.tasks = {}

        for task in project.gantt_chart:
            if task.get("name") == task_name and sorted(task.get("assigned_to", [])) == sorted(assigned_to):
                end_date = task.get("end_date")
                if not end_date:
                    continue

                # Prepare profile picture data
                assigned_users_with_pic = []
                for username in assigned_to:
                    user = db.get_user(username)
                    if user:
                        if user.profile_pic_data:
                            encoded = base64.b64encode(user.profile_pic_data).decode()
                            mime_type = "image/png" if user.profile_pic_data[:2] != b'\xff\xd8' else "image/jpeg"
                            profile_pic = f"data:{mime_type};base64,{encoded}"
                        else:
                            profile_pic = "/static/profile_pics/default.png"

                        assigned_users_with_pic.append({
                            "username": user.username,
                            "profile_pic": profile_pic
                        })

                calendar_task = {
                    "id": f"gantt-{task['row_num']}",
                    "date": end_date,
                    "description": task["name"],
                    "user": "system",
                    "color": "#fbc02d",
                    "label": "Deadline",
                    "time": None,
                    "is_all_day": True,
                    "assigned_to": assigned_users_with_pic,
                    "start_date": task.get("start_date", ""),
                    "predecessor": task.get("predecessor", ""),
                    "completed_seconds": completed_seconds,
                    "hours_to_complete": int(task.get("hours_to_complete", 1))
                }

                # Replace existing Gantt calendar task
                project.tasks[end_date] = [
                    t for t in project.tasks.get(end_date, [])
                    if not t["id"].startswith("gantt-") or t["id"] != calendar_task["id"]
                ]
                project.tasks[end_date].append(calendar_task)

                # ✅ Send update to calendar
                if project_code in task_connections:
                    for connection in task_connections[project_code]:
                        await connection.send_json({
                            "action": "task_update",
                            "task": calendar_task
                        })

        project._p_changed = True
        transaction.commit()

        return {"success": success, "message": message}

    print("❌ Task not found in the database!")
    return {"success": False, "message": "Task not found"}


@app.get("/get_task_progress")
async def get_task_progress(project_code: str, task_name: str, assigned_to: str):
    project = db.get_project(project_code)
    if not project:
        return {"success": False, "message": "Invalid project"}

    assigned_users = assigned_to.split(",")

    for task in project.gantt_chart:
        if task.get("name") == task_name and sorted(task.get("assigned_to", [])) == sorted(assigned_users):
            return {"success": True, "completed_seconds": task.get("completed_seconds", 0)}

    return {"success": False, "message": "Task not found"}









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




@app.post("/add_task")
async def add_task(
    request: Request,
    project_code: str = Body(...),
    date: str = Body(...),
    task: str = Body(...),
    user: str = Body(...),
    color: str = Body("#888"),
    label: str = Body("General"),
    time: str = Body(None),
    is_all_day: bool = Body(False)
):
    """Add a task and notify connected WebSocket clients."""
    project = db.get_project(project_code)
    if not project:
        return JSONResponse(content={"error": "Project not found"}, status_code=404)

    task_entry = {
        "id": str(uuid.uuid4()),
        "date": date,
        "description": task,
        "user": user,
        "color": color,
        "label": label,
        "time": time,
        "is_all_day": is_all_day
    }

    if not hasattr(project, "tasks"):
        project.tasks = {}

    if date not in project.tasks:
        project.tasks[date] = []

    # Sort tasks by time
    project.tasks[date].append(task_entry)
    if not is_all_day:
        project.tasks[date].sort(key=lambda x: (
            x.get("is_all_day", False),  # All-day tasks first
            x.get("time", "23:59pm")     # Then sort by time
        ))

    project._p_changed = True
    transaction.commit()

    # Notify all connected clients about the new task
    if project_code in task_connections:
        for connection in task_connections[project_code]:
            try:
                await connection.send_json({
                    "action": "new_task",
                    "task": task_entry
                })
            except Exception as e:
                print(f"WebSocket error: {e}")
                task_connections[project_code].remove(connection)

    return JSONResponse(content={"message": "Task added successfully", "task": task_entry})

@app.post("/update_task")
async def update_task(
    request: Request,
    project_code: str = Body(...),
    task_id: str = Body(...),
    date: str = Body(...),
    task: str = Body(...),
    color: str = Body("#888"),
    label: str = Body("General"),
    time: str = Body(None),
    is_all_day: bool = Body(False),
    is_complete: bool = Body(False)  # ✅ Add this to detect if task is marked complete
):
    """Update or complete an existing task and notify all team members."""
    project = db.get_project(project_code)
    if not project:
        return JSONResponse(content={"error": "Project not found"}, status_code=404)

    if not hasattr(project, "tasks") or date not in project.tasks:
        return JSONResponse(content={"error": "Task not found"}, status_code=404)

    # === ✅ If task is marked complete, delete it ===
    if is_complete:
        project.tasks[date] = [t for t in project.tasks[date] if t["id"] != task_id]
        project._p_changed = True
        transaction.commit()

        # Broadcast task deletion
        if project_code in task_connections:
            for connection in task_connections[project_code]:
                try:
                    await connection.send_json({
                        "action": "task_delete",
                        "task_id": task_id
                    })
                except Exception as e:
                    print(f"WebSocket error: {e}")
                    task_connections[project_code].remove(connection)

        return JSONResponse(content={"message": "Task marked complete and deleted"})

    # === ✅ Else, perform regular task update ===
    for task_entry in project.tasks[date]:
        if task_entry["id"] == task_id:
            task_entry.update({
                "description": task,
                "color": color,
                "label": label,
                "time": time,
                "is_all_day": is_all_day
            })

            if not is_all_day:
                project.tasks[date].sort(key=lambda x: (
                    x.get("is_all_day", False),
                    x.get("time", "23:59pm")
                ))

            project._p_changed = True
            transaction.commit()

            if project_code in task_connections:
                for connection in task_connections[project_code]:
                    try:
                        await connection.send_json({
                            "action": "task_update",
                            "task": task_entry
                        })
                    except Exception as e:
                        print(f"WebSocket error: {e}")
                        task_connections[project_code].remove(connection)

            return JSONResponse(content={"message": "Task updated successfully", "task": task_entry})

    return JSONResponse(content={"error": "Task not found"}, status_code=404)


@app.post("/delete_task")
async def delete_task(
    request: Request,
    project_code: str = Body(...),
    task_id: str = Body(...),
    date: str = Body(...)
):
    """Delete a task and notify all team members."""
    project = db.get_project(project_code)
    if not project:
        return JSONResponse(content={"error": "Project not found"}, status_code=404)

    if not hasattr(project, "tasks") or date not in project.tasks:
        return JSONResponse(content={"error": "Task not found"}, status_code=404)

    # Find and remove the task
    project.tasks[date] = [t for t in project.tasks[date] if t["id"] != task_id]
    project._p_changed = True
    transaction.commit()

    # Notify all connected clients about the task deletion
    if project_code in task_connections:
        for connection in task_connections[project_code]:
            try:
                await connection.send_json({
                    "action": "task_delete",
                    "task_id": task_id
                })
            except Exception as e:
                print(f"WebSocket error: {e}")
                task_connections[project_code].remove(connection)

    return JSONResponse(content={"message": "Task deleted successfully"})

# @app.get("/get_tasks/{project_code}/{date}")
# async def get_tasks(project_code: str, date: str):
#     """Get all tasks for a specific date."""
#     project = db.get_project(project_code)
#     if not project:
#         return JSONResponse(content={"error": "Project not found"}, status_code=404)

#     tasks = project.tasks.get(date, []) if hasattr(project, "tasks") else []
#     return JSONResponse(content={"tasks": tasks})


@app.get("/get_tasks_for_month/{project_code}/{year}/{month}")
async def get_tasks_for_month(project_code: str, year: int, month: int):
    """Get all tasks for a specific month."""
    project = db.get_project(project_code)
    if not project or not hasattr(project, "tasks"):
        return JSONResponse(content={"tasks": []})

    tasks_in_month = []

    for date_str, task_list in project.tasks.items():
        try:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            if date_obj.year == year and date_obj.month == month:
                tasks_in_month.extend(task_list)
        except Exception as e:
            print(f"Error parsing date {date_str}: {e}")
            continue

    return JSONResponse(content={"tasks": tasks_in_month})

@app.post("/upload_file")
async def upload_file(
    file: UploadFile = File(...),
    chat_id: str = Form(...),
    user: str = Form(...),
    project_code: str = Query(...)
):
    """Handle file uploads for chat messages."""
    try:
        # Validate project and user
        project = db.get_project(project_code)
        if not project:
            return JSONResponse(
                content={"success": False, "error": "Project not found"},
                status_code=404
            )

        chat = project.get_chat(chat_id)
        if not chat or user not in chat.participants:
            return JSONResponse(
                content={"success": False, "error": "Chat not found or unauthorized"},
                status_code=403
            )

        # Generate unique filename while preserving extension
        file_ext = Path(file.filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = UPLOAD_DIR / unique_filename

        # Save the file
        try:
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            print(f"✅ File saved successfully: {unique_filename}")
            return JSONResponse(content={
                "success": True,
                "filename": unique_filename,
                "original_name": file.filename
            })
        except Exception as e:
            print(f"❌ File save error: {str(e)}")
            return JSONResponse(
                content={"success": False, "error": f"File save error: {str(e)}"},
                status_code=500
            )

    except Exception as e:
        print(f"❌ File upload error: {str(e)}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )
    

@app.get("/project_notes/{project_code}")
async def get_notes(project_code: str):
    project = db.get_project(project_code)
    if not project:
        return {"notes": []}

    if not hasattr(project, 'postit_notes'):
        project.postit_notes = PersistentList()
        transaction.commit()

    return {"notes": list(project.postit_notes)}

@app.post("/project_notes/{project_code}/add")
async def add_note(project_code: str, data: dict = Body(...)):
    project = db.get_project(project_code)
    if not project:
        return {"success": False}

    note = project.add_postit_note(data["content"], data["x"], data["y"], data.get("color"))
    return {"success": True, "note": note}

@app.post("/project_notes/{project_code}/update")
async def update_note_content(project_code: str, data: dict = Body(...)):
    project = db.get_project(project_code)
    if not project:
        return {"success": False, "message": "Project not found"}

    try:
        note_id = data["id"]
        new_content = data["content"]  # The updated content from the frontend
        
        # Find the note and update its content
        note = next((note for note in project.postit_notes if note["id"] == note_id), None)
        if note:
            note["content"] = new_content  # Update content
            project._p_changed = True
            transaction.commit()
            return {"success": True, "message": "Note updated successfully"}
        else:
            return {"success": False, "message": "Note not found"}
    
    except KeyError as e:
        return {"success": False, "message": f"Missing key: {str(e)}"}


@app.post("/project_notes/{project_code}/delete")
async def delete_note(project_code: str, data: dict = Body(...)):
    project = db.get_project(project_code)
    if not project:
        return {"success": False}

    if not hasattr(project, 'postit_notes'):
        project.postit_notes = PersistentList()
        transaction.commit()

    project.delete_postit_note(data["id"])
    return {"success": True}