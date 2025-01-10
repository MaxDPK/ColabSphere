from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from models import User, Project, db

app = FastAPI()


# Templates and static files setup
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = request.cookies.get("user")
    if user:
        return RedirectResponse("/menu", status_code=303)
    return RedirectResponse("/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    user = db.get_user(username)
    if user and user.password == password:
        response = RedirectResponse("/menu", status_code=303)
        response.set_cookie(key="user", value=username)
        return response
    return "Invalid credentials"

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "signup": True})

@app.post("/signup")
async def signup(username: str = Form(...), password: str = Form(...)):
    if db.add_user(username, password):
        response = RedirectResponse("/menu", status_code=303)
        response.set_cookie(key="user", value=username)
        return response
    return "User already exists"

@app.get("/menu", response_class=HTMLResponse)
async def menu(request: Request):
    user = request.cookies.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    user_data = db.get_user(user)
    projects = db.get_user_projects(user_data.username)
    return templates.TemplateResponse("menu.html", {"request": request, "projects": projects, "user": user})

@app.post("/create_project")
async def create_project(request: Request, project_name: str = Form(...)):
    user = request.cookies.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    project_code = db.create_project(project_name, user)
    response = RedirectResponse("/menu", status_code=303)
    # Optionally, you can flash a message or display the project code
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
async def project_hub(request: Request, project_code: str):
    user = request.cookies.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    project = db.get_project(project_code)
    if not project:
        return "Invalid project"
    if user not in project.members:
        return "You are not a member of this project"
    return templates.TemplateResponse("project_hub.html", {"request": request, "project": project})

@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("user")
    return response


