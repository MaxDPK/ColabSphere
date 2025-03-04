from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query, Form, Depends, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from models import User, Project, db
from typing import Dict, List
import json

app = FastAPI()


# Templates and static files setup
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Track online users and WebSocket connections per project
online_users: Dict[str, set] = {}
project_connections: Dict[str, List[WebSocket]] = {}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = request.cookies.get("user")
    if user:
        return RedirectResponse(f"/menu?user={user}", status_code=303)
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
        # Show user-friendly error message and stay on the same page
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "signup": True,
            "error_message": "Passwords do not match. Please try again."  # Pass the error message to the template
        })
    
    # Check if the user already exists
    if db.get_user(username):
        # Show user-friendly error message for existing username
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "signup": True,
            "error_message": "User already exists. Please try a different username."  # Pass the error message to the template
        })
    
    # Proceed with adding the user if passwords match and username is available
    if db.add_user(username, password):
        # Redirect to login page after successful signup
        return RedirectResponse("/login", status_code=303)
    
    return "An unexpected error occurred."




@app.get("/menu", response_class=HTMLResponse)
async def menu(request: Request, user: str):
    user_data = db.get_user(user)
    if not user_data:
        return RedirectResponse("/login", status_code=303)
    projects = db.get_user_projects(user_data.username)
    return templates.TemplateResponse("menu.html", {"request": request, "projects": projects, "user": user})



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

    print(f"All Tasks: {json.dumps(all_tasks, indent=2)}")  # ✅ Debug all tasks
    print(f"Assigned Tasks for {user}: {json.dumps(assigned_tasks, indent=2)}")  # ✅ Debug assigned tasks

    return templates.TemplateResponse("project_hub.html", {
        "request": request,
        "project": project,
        "members": project.members,
        "user": user,
        "assigned_tasks": assigned_tasks,
        "all_tasks": all_tasks  # ✅ Send all tasks for reference
    })





active_users_per_task: Dict[str, int] = {}  # Track active workers per task

@app.websocket("/ws/{project_code}")
async def websocket_endpoint(websocket: WebSocket, project_code: str, user: str = Query(...)):
    await websocket.accept()

    if project_code not in project_connections:
        project_connections[project_code] = []
    project_connections[project_code].append(websocket)

    if project_code not in online_users:
        online_users[project_code] = set()
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
                # ✅ Increase active worker count per task
                active_users_per_task[task_key] = active_users_per_task.get(task_key, 0) + 1
                print(f"🟢 {active_users_per_task[task_key]} users working on {task_name}")

                # ✅ Broadcast the active worker count
                await broadcast_active_users(project_code, task_name, assigned_to)

            elif action == "stop_work":
                if task_key in active_users_per_task:
                    active_users_per_task[task_key] -= 1
                    if active_users_per_task[task_key] <= 0:
                        del active_users_per_task[task_key]
                
                print(f"🔴 {active_users_per_task.get(task_key, 0)} users working on {task_name}")

                # ✅ Broadcast updated worker count
                await broadcast_active_users(project_code, task_name, assigned_to)

    except WebSocketDisconnect:
        project_connections[project_code].remove(websocket)
        online_users[project_code].remove(user)
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
        return {"activities": []}
    
    activities = getattr(project, "gantt_chart", [])

    for activity in activities:
        if isinstance(activity.get("assigned_to"), list):
            activity["assigned_to"] = ";".join(activity["assigned_to"])

        # ✅ Ensure `completed_seconds` is always present
        if "work_hours_per_day" in activity and "days" in activity:
            if "completed_seconds" not in activity:
                activity["completed_seconds"] = 0  # Default to 0

    return {"activities": activities}






@app.post("/save_gantt_chart")
async def save_gantt_chart(data: dict = Body(...)):
    project_code = data.get("project_code")
    activities = data.get("activities", [])

    for activity in activities:
        if isinstance(activity.get("assigned_to"), str):
            assigned_users = activity["assigned_to"].strip()
            activity["assigned_to"] = assigned_users.split(";") if assigned_users else []

        # ✅ Ensure `completed_seconds` is always initialized to 0 (for tasks, not milestones)
        if "work_hours_per_day" in activity and "days" in activity:
            if "completed_seconds" not in activity:
                activity["completed_seconds"] = 0  # Default to 0

    success, message = db.save_gantt_chart(project_code, activities)

    if success:
        # Notify WebSocket users
        if project_code in project_connections:
            for connection in project_connections[project_code]:
                await connection.send_json({
                    "action": "gantt_chart_update",
                    "activities": activities
                })
    return {"message": message}


@app.post("/update_task_progress")
async def update_task_progress(data: dict = Body(...)):
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

            # ✅ Only update if the value has actually changed
            if completed_seconds > prev_seconds:
                task["completed_seconds"] = completed_seconds  # Save in DB
                updated = True
                print(f"✅ Task match found: Updating completed_time from {prev_seconds}s to {completed_seconds}s")

    if updated and completed_seconds > prev_seconds:
        success, message = db.save_gantt_chart(project_code, project.gantt_chart)

        # ✅ Only send WebSocket update if the database save was successful
        if success and project_code in project_connections:
            update_message = {
                "action": "update_progress",
                "task_name": task_name,
                "assigned_to": assigned_to,
                "completed_seconds": completed_seconds,
                "formatted_time": f"{completed_seconds // 3600}h {(completed_seconds % 3600) // 60}m {completed_seconds % 60}s"
            }
            print(f"📡 Sending WebSocket update: {update_message}")

            for connection in project_connections[project_code]:
                await connection.send_json(update_message)

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


