import os
from datetime import datetime, date
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-this")

database_url = os.getenv("DATABASE_URL")

# Railway PostgreSQL sometimes gives postgres://, SQLAlchemy needs postgresql://
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# Local computer uses SQLite automatically.
# Railway uses PostgreSQL through DATABASE_URL.
app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:///taskflow.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# -------------------- MODELS --------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="member")  # admin/member

    created_projects = db.relationship("Project", backref="creator", lazy=True)
    assigned_tasks = db.relationship("Task", foreign_keys="Task.assigned_to", backref="assignee", lazy=True)


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    deadline = db.Column(db.Date, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    tasks = db.relationship("Task", backref="project", lazy=True, cascade="all, delete-orphan")


class ProjectMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    project = db.relationship("Project", backref="members")
    user = db.relationship("User", backref="project_memberships")


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    due_date = db.Column(db.Date, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    creator = db.relationship("User", foreign_keys=[created_by])


# -------------------- HELPERS --------------------

def current_user():
    if "user_id" not in session:
        return None
    return db.session.get(User, session["user_id"])


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "error")
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user.role != "admin":
            flash("Only admin can access this page.", "error")
            return redirect(url_for("dashboard"))
        return func(*args, **kwargs)
    return wrapper


def parse_date(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def dashboard_counts(user):
    today = date.today()

    if user.role == "admin":
        tasks_query = Task.query
    else:
        tasks_query = Task.query.filter_by(assigned_to=user.id)

    tasks = tasks_query.all()

    total = len(tasks)
    pending = len([t for t in tasks if t.status == "pending"])
    in_progress = len([t for t in tasks if t.status == "in_progress"])
    completed = len([t for t in tasks if t.status == "completed"])
    overdue = len([t for t in tasks if t.due_date < today and t.status != "completed"])
    progress = round((completed / total) * 100, 2) if total > 0 else 0

    return {
        "total": total,
        "pending": pending,
        "in_progress": in_progress,
        "completed": completed,
        "overdue": overdue,
        "progress": progress
    }


# -------------------- PAGE ROUTES --------------------

@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "member")

        if not name or not email or not password:
            flash("All fields are required.", "error")
            return redirect(url_for("signup"))

        if role not in ["admin", "member"]:
            flash("Invalid role selected.", "error")
            return redirect(url_for("signup"))

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Email already registered.", "error")
            return redirect(url_for("signup"))

        user = User(
            name=name,
            email=email,
            password=generate_password_hash(password),
            role=role
        )
        db.session.add(user)
        db.session.commit()

        flash("Signup successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        session["role"] = user.role

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    counts = dashboard_counts(user)

    if user.role == "admin":
        projects = Project.query.order_by(Project.id.desc()).all()
        tasks = Task.query.order_by(Task.due_date.asc()).all()
        return render_template("admin_dashboard.html", user=user, counts=counts, projects=projects, tasks=tasks)

    tasks = Task.query.filter_by(assigned_to=user.id).order_by(Task.due_date.asc()).all()
    return render_template("member_dashboard.html", user=user, counts=counts, tasks=tasks)


@app.route("/projects", methods=["GET", "POST"])
@login_required
@admin_required
def projects():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        deadline = request.form.get("deadline", "")

        if not title:
            flash("Project title is required.", "error")
            return redirect(url_for("projects"))

        project = Project(
            title=title,
            description=description,
            deadline=parse_date(deadline),
            created_by=session["user_id"]
        )
        db.session.add(project)
        db.session.commit()

        flash("Project created successfully.", "success")
        return redirect(url_for("projects"))

    all_projects = Project.query.order_by(Project.id.desc()).all()
    members = User.query.filter_by(role="member").all()
    return render_template("projects.html", projects=all_projects, members=members)


@app.route("/projects/<int:project_id>/add-member", methods=["POST"])
@login_required
@admin_required
def add_project_member(project_id):
    user_id = request.form.get("user_id")

    project = db.session.get(Project, project_id)
    member = User.query.filter_by(id=user_id, role="member").first()

    if not project:
        flash("Project not found.", "error")
        return redirect(url_for("projects"))

    if not member:
        flash("Invalid member selected.", "error")
        return redirect(url_for("projects"))

    existing = ProjectMember.query.filter_by(project_id=project.id, user_id=member.id).first()
    if existing:
        flash("Member already added to this project.", "error")
        return redirect(url_for("projects"))

    project_member = ProjectMember(project_id=project.id, user_id=member.id)
    db.session.add(project_member)
    db.session.commit()

    flash("Member added to project.", "success")
    return redirect(url_for("projects"))


@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    user = current_user()

    if request.method == "POST":
        if user.role != "admin":
            flash("Only admin can create tasks.", "error")
            return redirect(url_for("tasks"))

        project_id = request.form.get("project_id")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        assigned_to = request.form.get("assigned_to")
        due_date = request.form.get("due_date")

        if not project_id or not title or not assigned_to or not due_date:
            flash("Project, task title, assigned member, and due date are required.", "error")
            return redirect(url_for("tasks"))

        task = Task(
            project_id=int(project_id),
            title=title,
            description=description,
            assigned_to=int(assigned_to),
            status="pending",
            due_date=parse_date(due_date),
            created_by=user.id
        )
        db.session.add(task)
        db.session.commit()

        flash("Task created successfully.", "success")
        return redirect(url_for("tasks"))

    if user.role == "admin":
        all_tasks = Task.query.order_by(Task.due_date.asc()).all()
        projects = Project.query.all()
        members = User.query.filter_by(role="member").all()
    else:
        all_tasks = Task.query.filter_by(assigned_to=user.id).order_by(Task.due_date.asc()).all()
        projects = []
        members = []

    return render_template("tasks.html", user=user, tasks=all_tasks, projects=projects, members=members)


@app.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    user = current_user()
    task = db.session.get(Task, task_id)

    if not task:
        flash("Task not found.", "error")
        return redirect(url_for("tasks"))

    if user.role != "admin" and task.assigned_to != user.id:
        flash("You can update only your assigned task.", "error")
        return redirect(url_for("tasks"))

    status = request.form.get("status")
    if status not in ["pending", "in_progress", "completed"]:
        flash("Invalid status.", "error")
        return redirect(url_for("tasks"))

    task.status = status
    db.session.commit()

    flash("Task status updated.", "success")
    return redirect(url_for("tasks"))


# -------------------- REST API ROUTES --------------------

@app.route("/api/projects", methods=["GET"])
@login_required
def api_get_projects():
    user = current_user()

    if user.role == "admin":
        projects = Project.query.all()
    else:
        memberships = ProjectMember.query.filter_by(user_id=user.id).all()
        project_ids = [m.project_id for m in memberships]
        projects = Project.query.filter(Project.id.in_(project_ids)).all() if project_ids else []

    return jsonify([
        {
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "deadline": p.deadline.isoformat() if p.deadline else None
        }
        for p in projects
    ])


@app.route("/api/tasks", methods=["GET"])
@login_required
def api_get_tasks():
    user = current_user()

    if user.role == "admin":
        tasks = Task.query.all()
    else:
        tasks = Task.query.filter_by(assigned_to=user.id).all()

    return jsonify([
        {
            "id": t.id,
            "project": t.project.title,
            "title": t.title,
            "assigned_to": t.assignee.name,
            "status": t.status,
            "due_date": t.due_date.isoformat()
        }
        for t in tasks
    ])


@app.route("/api/dashboard", methods=["GET"])
@login_required
def api_dashboard():
    user = current_user()
    return jsonify(dashboard_counts(user))


# -------------------- DATABASE CREATION + DEMO USERS --------------------

def seed_demo_data():
    admin = User.query.filter_by(email="admin@test.com").first()
    member = User.query.filter_by(email="member@test.com").first()

    if not admin:
        admin = User(
            name="Demo Admin",
            email="admin@test.com",
            password=generate_password_hash("admin123"),
            role="admin"
        )
        db.session.add(admin)

    if not member:
        member = User(
            name="Demo Member",
            email="member@test.com",
            password=generate_password_hash("member123"),
            role="member"
        )
        db.session.add(member)

    db.session.commit()


with app.app_context():
    db.create_all()
    seed_demo_data()


if __name__ == "__main__":
    app.run(debug=True)
