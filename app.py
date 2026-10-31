import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, redirect, url_for, flash, request, abort
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm, CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from wtforms import StringField, PasswordField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError, Regexp

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-this-before-deploying")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///umang_social.db")
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["SQLALCHEMY_DATABASE_URI"].replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode="threading")
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"], storage_uri="memory://")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

def now_utc():
    return datetime.now(timezone.utc)

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(24), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    bio = db.Column(db.String(160), default="")
    avatar = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def post_count(self):
        return Post.query.filter_by(author_id=self.id).count()

    @property
    def follower_count(self):
        return Follow.query.filter_by(followed_id=self.id).count()

    @property
    def following_count(self):
        return Follow.query.filter_by(follower_id=self.id).count()

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    body = db.Column(db.String(1000), default="")
    image = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False, index=True)
    author = db.relationship("User", backref="posts")

class DirectMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    body = db.Column(db.String(2000), nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False, index=True)
    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.String(400), nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)
    author = db.relationship("User")
    post = db.relationship("Post", backref=db.backref("comments", cascade="all, delete-orphan", lazy=True))

class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post = db.relationship("Post", backref=db.backref("likes", cascade="all, delete-orphan", lazy=True))
    __table_args__ = (db.UniqueConstraint("post_id", "user_id"),)

class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint("follower_id", "followed_id"),)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"))
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)
    seen = db.Column(db.Boolean, default=False)
    actor = db.relationship("User", foreign_keys=[actor_id])

class Block(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint("blocker_id", "blocked_id"),)

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"))
    reported_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reason = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[
        DataRequired(), Length(min=3, max=24),
        Regexp(r"^[A-Za-z0-9_]+$", message="Use only letters, numbers and underscores.")
    ])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=10, max=128)])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Create account")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data.lower()).first():
            raise ValidationError("That username is already taken.")

class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")

class PostForm(FlaskForm):
    body = TextAreaField("What's happening?", validators=[Length(max=1000)])
    submit = SubmitField("Share post")

class CommentForm(FlaskForm):
    body = StringField("Write a comment", validators=[DataRequired(), Length(min=1, max=400)])
    submit = SubmitField("Comment")

class ProfileForm(FlaskForm):
    bio = TextAreaField("About you", validators=[Length(max=160)])
    submit = SubmitField("Save profile")

def blocked_user_ids(user_id):
    outgoing = [b.blocked_id for b in Block.query.filter_by(blocker_id=user_id).all()]
    incoming = [b.blocker_id for b in Block.query.filter_by(blocked_id=user_id).all()]
    return set(outgoing + incoming)

def visible_posts_query():
    blocked = blocked_user_ids(current_user.id)
    q = Post.query
    if blocked:
        q = q.filter(~Post.author_id.in_(blocked))
    return q

def make_notification(user_id, actor_id, kind, post_id=None):
    if user_id != actor_id:
        db.session.add(Notification(user_id=user_id, actor_id=actor_id, kind=kind, post_id=post_id))

@app.context_processor
def inject_globals():
    if current_user.is_authenticated:
        unread = Notification.query.filter_by(user_id=current_user.id, seen=False).count()
    else:
        unread = 0
    return {"unread_notifications": unread}

@app.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template("landing.html")
    posts = visible_posts_query().order_by(Post.created_at.desc()).limit(60).all()
    following_ids = {f.followed_id for f in Follow.query.filter_by(follower_id=current_user.id).all()}
    liked_ids = {l.post_id for l in Like.query.filter_by(user_id=current_user.id).all()}
    comment_forms = {p.id: CommentForm(prefix=f"c{p.id}") for p in posts}
    return render_template("feed.html", posts=posts, following_ids=following_ids,
                           liked_ids=liked_ids, comment_forms=comment_forms, post_form=PostForm())

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    form = RegisterForm()
    if form.validate_on_submit():
        user = User(username=form.username.data.lower())
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Welcome to UMANG SOCIAL. Your account is ready!", "success")
        return redirect(url_for("index"))
    return render_template("auth.html", form=form, mode="register")

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(request.args.get("next") or url_for("index"))
        flash("Username or password is incorrect.", "error")
    return render_template("auth.html", form=form, mode="login")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

@app.route("/post", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def create_post():
    form = PostForm()
    body = (request.form.get("body") or "").strip()
    image = request.files.get("image")
    filename = ""
    if image and image.filename:
        if not allowed_file(image.filename):
            flash("Please upload a PNG, JPG or WEBP image.", "error")
            return redirect(url_for("index"))
        filename = secrets.token_hex(16) + "." + secure_filename(image.filename).rsplit(".", 1)[1].lower()
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    if not body and not filename:
        flash("Write something or attach an image before posting.", "error")
        return redirect(url_for("index"))
    post = Post(author_id=current_user.id, body=body, image=filename)
    db.session.add(post)
    db.session.commit()
    flash("Your post is live.", "success")
    return redirect(url_for("index"))

@app.route("/like/<int:post_id>", methods=["POST"])
@login_required
def like(post_id):
    post = db.session.get(Post, post_id) or abort(404)
    if post.author_id in blocked_user_ids(current_user.id):
        abort(404)
    existing = Like.query.filter_by(post_id=post.id, user_id=current_user.id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(Like(post_id=post.id, user_id=current_user.id))
        make_notification(post.author_id, current_user.id, "liked", post.id)
    db.session.commit()
    return redirect(request.referrer or url_for("index"))

@app.route("/comment/<int:post_id>", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def comment(post_id):
    post = db.session.get(Post, post_id) or abort(404)
    if post.author_id in blocked_user_ids(current_user.id):
        abort(404)
    body = (request.form.get("body") or "").strip()
    if body and len(body) <= 400:
        db.session.add(Comment(post_id=post.id, author_id=current_user.id, body=body))
        make_notification(post.author_id, current_user.id, "commented", post.id)
        db.session.commit()
    return redirect(request.referrer or url_for("index"))

@app.route("/user/<username>")
def profile(username):
    user = User.query.filter_by(username=username.lower()).first_or_404()
    if current_user.is_authenticated and user.id in blocked_user_ids(current_user.id):
        abort(404)
    posts = Post.query.filter_by(author_id=user.id).order_by(Post.created_at.desc()).limit(40).all()
    is_following = current_user.is_authenticated and Follow.query.filter_by(follower_id=current_user.id, followed_id=user.id).first() is not None
    return render_template("profile.html", profile_user=user, posts=posts, is_following=is_following)

@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id or user.id in blocked_user_ids(current_user.id):
        abort(400)
    existing = Follow.query.filter_by(follower_id=current_user.id, followed_id=user.id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(Follow(follower_id=current_user.id, followed_id=user.id))
        make_notification(user.id, current_user.id, "followed")
    db.session.commit()
    return redirect(request.referrer or url_for("profile", username=user.username))

@app.route("/explore")
@login_required
def explore():
    q = (request.args.get("q") or "").strip()[:40]
    blocked = blocked_user_ids(current_user.id)
    users = User.query.filter(User.id != current_user.id)
    if blocked:
        users = users.filter(~User.id.in_(blocked))
    if q:
        users = users.filter(User.username.contains(q.lower()))
    users = users.order_by(User.username.asc()).limit(50).all()
    following_ids = {f.followed_id for f in Follow.query.filter_by(follower_id=current_user.id).all()}
    return render_template("explore.html", users=users, following_ids=following_ids, q=q)

@app.route("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(60).all()
    for item in items:
        item.seen = True
    db.session.commit()
    return render_template("notifications.html", items=items)

@app.route("/edit-profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    form = ProfileForm()
    if request.method == "GET":
        form.bio.data = current_user.bio
    if form.validate_on_submit():
        current_user.bio = form.bio.data.strip()
        avatar = request.files.get("avatar")
        if avatar and avatar.filename:
            if not allowed_file(avatar.filename):
                flash("Use PNG, JPG or WEBP for your profile photo.", "error")
                return render_template("edit_profile.html", form=form)
            filename = secrets.token_hex(16) + "." + secure_filename(avatar.filename).rsplit(".", 1)[1].lower()
            avatar.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            current_user.avatar = filename
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile", username=current_user.username))
    return render_template("edit_profile.html", form=form)

@app.route("/report", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def report():
    post_id = request.form.get("post_id", type=int)
    user_id = request.form.get("user_id", type=int)
    reason = (request.form.get("reason") or "Other").strip()[:300]
    if post_id and db.session.get(Post, post_id):
        db.session.add(Report(reporter_id=current_user.id, post_id=post_id, reason=reason))
    elif user_id and db.session.get(User, user_id) and user_id != current_user.id:
        db.session.add(Report(reporter_id=current_user.id, reported_user_id=user_id, reason=reason))
    else:
        abort(400)
    db.session.commit()
    flash("Thanks. Your report has been recorded for review.", "success")
    return redirect(request.referrer or url_for("index"))

@app.route("/block/<int:user_id>", methods=["POST"])
@login_required
def block(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        abort(400)
    if not Block.query.filter_by(blocker_id=current_user.id, blocked_id=user.id).first():
        db.session.add(Block(blocker_id=current_user.id, blocked_id=user.id))
    Follow.query.filter(or_(
        (Follow.follower_id == current_user.id) & (Follow.followed_id == user.id),
        (Follow.follower_id == user.id) & (Follow.followed_id == current_user.id)
    )).delete(synchronize_session=False)
    db.session.commit()
    flash("User blocked. Their content is hidden from you.", "info")
    return redirect(url_for("index"))

@app.route("/messages")
@login_required
def messages():
    q = (request.args.get("q") or "").strip().lower()[:24]
    users = User.query.filter(User.id != current_user.id)
    blocked = blocked_user_ids(current_user.id)
    if blocked:
        users = users.filter(~User.id.in_(blocked))
    if q:
        users = users.filter(User.username.contains(q))
    users = users.order_by(User.username.asc()).limit(30).all()
    # Show recent conversation partners, without exposing message text in the list.
    rows = DirectMessage.query.filter(or_(
        DirectMessage.sender_id == current_user.id,
        DirectMessage.recipient_id == current_user.id
    )).order_by(DirectMessage.created_at.desc()).limit(200).all()
    partner_ids = []
    for row in rows:
        other_id = row.recipient_id if row.sender_id == current_user.id else row.sender_id
        if other_id not in partner_ids and other_id not in blocked:
            partner_ids.append(other_id)
    recent_users = [db.session.get(User, uid) for uid in partner_ids[:20]]
    recent_users = [u for u in recent_users if u]
    return render_template("messages.html", users=users, recent_users=recent_users, q=q)

@app.route("/chat/<username>")
@login_required
def chat(username):
    other = User.query.filter_by(username=username.lower()).first_or_404()
    if other.id == current_user.id or other.id in blocked_user_ids(current_user.id):
        abort(404)
    history = DirectMessage.query.filter(or_(
        (DirectMessage.sender_id == current_user.id) & (DirectMessage.recipient_id == other.id),
        (DirectMessage.sender_id == other.id) & (DirectMessage.recipient_id == current_user.id)
    )).order_by(DirectMessage.created_at.asc()).limit(300).all()
    return render_template("chat.html", other=other, history=history)

def conversation_room(user_a, user_b):
    low, high = sorted((int(user_a), int(user_b)))
    return f"dm_{low}_{high}"

@socketio.on("join_chat")
def on_join_chat(data):
    if not current_user.is_authenticated:
        emit("chat_error", {"message": "Please log in again."})
        return
    username = (data or {}).get("username", "")
    other = User.query.filter_by(username=str(username).lower()).first()
    if not other or other.id == current_user.id or other.id in blocked_user_ids(current_user.id):
        emit("chat_error", {"message": "This chat is not available."})
        return
    join_room(conversation_room(current_user.id, other.id))
    emit("joined_chat", {"username": other.username})

@socketio.on("send_message")
@limiter.limit("30 per minute")
def on_send_message(data):
    if not current_user.is_authenticated:
        emit("chat_error", {"message": "Please log in again."})
        return
    data = data or {}
    username = str(data.get("username", "")).lower()[:24]
    body = str(data.get("body", "")).strip()
    other = User.query.filter_by(username=username).first()
    if not other or other.id == current_user.id or other.id in blocked_user_ids(current_user.id):
        emit("chat_error", {"message": "You cannot message this account."})
        return
    if not body or len(body) > 2000:
        emit("chat_error", {"message": "Message must be between 1 and 2000 characters."})
        return
    message = DirectMessage(sender_id=current_user.id, recipient_id=other.id, body=body)
    db.session.add(message)
    db.session.commit()
    payload = {
        "id": message.id,
        "sender": current_user.username,
        "recipient": other.username,
        "body": message.body,
        "created_at": message.created_at.strftime("%d %b %H:%M UTC"),
    }
    emit("new_message", payload, room=conversation_room(current_user.id, other.id))

@app.errorhandler(413)
def too_large(_):
    flash("Image is too large. Maximum upload size is 5 MB.", "error")
    return redirect(request.referrer or url_for("index"))

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, allow_unsafe_werkzeug=True)
