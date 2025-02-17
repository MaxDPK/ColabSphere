from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query, Form, Depends, Body, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from models import User, Project, db
from typing import Dict, List
import shutil
import os
from io import BytesIO
import base64

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
        return RedirectResponse(f"/choose_profile_pic?user={username}", status_code=303)
    
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
    
    return templates.TemplateResponse("project_hub.html", {
        "request": request,
        "project": project,
        "members": project.members,
        "user": user
    })



@app.websocket("/ws/{project_code}")
async def websocket_endpoint(websocket: WebSocket, project_code: str, user: str = Query(...)):
    await websocket.accept()

    # Add the WebSocket to the project's connection list
    if project_code not in project_connections:
        project_connections[project_code] = []
    project_connections[project_code].append(websocket)

    # Add the user to the online list for the project
    if project_code not in online_users:
        online_users[project_code] = set()
    online_users[project_code].add(user)

    # Notify all connected clients about the updated online users
    await notify_project_users(project_code)

    try:
        # Keep the WebSocket connection alive
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        # Handle WebSocket disconnection
        project_connections[project_code].remove(websocket)
        online_users[project_code].remove(user)

        # Notify remaining clients about the updated online users
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
