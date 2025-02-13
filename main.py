from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from models import User, Project, db
from typing import Dict, List

app = FastAPI()


# Templates and static files setup
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Track online users and WebSocket connections per project
online_users: Dict[str, set] = {}
project_connections: Dict[str, List[WebSocket]] = {}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Always redirect to /login when visiting the root URL
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
    # Ensure that the user is logged in
    user = request.cookies.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    
    # Log project_name for debugging
    print(f"Received project name: {project_name}")
    
    # Call the database function to create the project
    project_code = db.create_project(project_name, user)
    
    # Redirect to the menu page with the user query parameter
    response = RedirectResponse(f"/menu?user={user}", status_code=303)
    return response



@app.post("/join_project")
async def join_project(request: Request, project_code: str = Form(...)):
    user = request.cookies.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    success = db.add_user_to_project(user, project_code)
    if success:
        return RedirectResponse("/menu", status_code=303)
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

@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("user")
    return response


