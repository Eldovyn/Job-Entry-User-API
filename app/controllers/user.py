from ..databases import UserDatabase, TokenBlacklistDatabase, AccountActiveDatabase
from flask import jsonify, url_for
import sqlalchemy
from flask_jwt_extended import create_access_token
import re
import datetime
from ..utils import TokenAccountActiveEmail, TokenAccountActiveWeb, generate_id
from ..task import send_email_task
from ..config import job_entry_url


class UserController:
    @staticmethod
    async def update_password(user_id, new_password, confirm_password):
        from ..bcrypt import bcrypt

        errors = {}
        if len(new_password.strip()) == 0:
            errors["password"] = ["password cannot be empty"]
        if len(confirm_password.strip()) == 0:
            if "confirm_password" in errors:
                errors["confirm_password"] = ["confirm_password cannot be empty"]
            else:
                errors["confirm_password"] = ["confirm_password cannot be empty"]
        if new_password != confirm_password:
            if "confirm_password" in errors:
                errors["confirm_password"].append("confirm password not match")
            else:
                errors["confirm_password"] = ["confirm password not match"]
        if errors:
            return (
                jsonify(
                    {
                        "message": "input invalid",
                        "errors": errors,
                    }
                ),
                400,
            )
        if not (user := await UserDatabase.get("user_id", user_id=user_id)):
            return (
                jsonify({"message": "authorization invalid"}),
                401,
            )
        new_password = bcrypt.generate_password_hash(new_password)
        await UserDatabase.update(
            "password", user_id=user_id, new_password=new_password
        )
        return (
            jsonify(
                {
                    "message": "success update password",
                    "data": {
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "is_active": user.is_active,
                    },
                }
            ),
            201,
        )

    @staticmethod
    async def update_user(user_id, username, email):
        errors = {}
        if len(username.strip()) == 0:
            errors["new_username"] = ["username cannot be empty"]
        if len(email.strip()) == 0:
            errors["new_email"] = ["email cannot be empty"]
        if errors:
            return (
                jsonify(
                    {
                        "message": "input invalid",
                        "errors": errors,
                    }
                ),
                400,
            )
        if not (user := await UserDatabase.get("user_id", user_id=user_id)):
            return (
                jsonify({"message": "authorization invalid"}),
                401,
            )
        result = await UserDatabase.update(
            "username_email", user_id=user_id, new_username=username, new_email=email
        )
        data = {
            "id": user.id,
            "username": username,
            "email": email,
            "is_active": user.is_active,
        }
        if result.username != user.username:
            data["new_username"] = username
        if result.email != user.email:
            data["new_email"] = email
        return (
            jsonify(
                {
                    "message": "success update user",
                    "data": data,
                }
            ),
            201,
        )

    @staticmethod
    async def update_user_username(user_id, new_username):
        if len(new_username.strip()) == 0:
            return (
                jsonify(
                    {
                        "message": "input cannot be empty",
                        "errors": {"new_username": ["new username cannot be empty"]},
                    }
                ),
                400,
            )
        if not (user := await UserDatabase.get("user_id", user_id=user_id)):
            return (
                jsonify({"message": "authorization invalid"}),
                401,
            )
        await UserDatabase.update(
            "username", user_id=user_id, new_username=new_username
        )
        return (
            jsonify(
                {
                    "message": "success update username",
                    "data": {
                        "new_username": new_username,
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "is_active": user.is_active,
                    },
                }
            ),
            201,
        )

    @staticmethod
    async def update_user_email(user_id, new_email):
        if len(new_email.strip()) == 0:
            return (
                jsonify(
                    {
                        "message": "input cannot be empty",
                        "errors": {"new_email": ["new email cannot be empty"]},
                    }
                ),
                400,
            )
        if not (user := await UserDatabase.get("user_id", user_id=user_id)):
            return (
                jsonify({"message": "authorization invalid"}),
                401,
            )
        await UserDatabase.update("email", user_id=user_id, new_email=new_email)
        return (
            jsonify(
                {
                    "message": "success update email",
                    "data": {
                        "new_email": new_email,
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "is_active": user.is_active,
                    },
                }
            ),
            201,
        )

    @staticmethod
    async def user_me(user_id):
        if not (user := await UserDatabase.get("user_id", user_id=user_id)):
            return (
                jsonify({"message": "authorization invalid"}),
                401,
            )
        return (
            jsonify(
                {
                    "message": "success get user",
                    "data": {
                        "user_id": user.user_id,
                        "username": user.username,
                        "email": user.email,
                        "is_active": user.is_active,
                        "avatar": user.user_avatar.avatar,
                    },
                }
            ),
            200,
        )

    @staticmethod
    async def user_logout(jti):
        await TokenBlacklistDatabase.insert(
            generate_id(), jti, datetime.datetime.now(datetime.timezone.utc).timestamp()
        )
        return jsonify({"message": "success logout"}), 201

    @staticmethod
    async def user_login(email, password):
        from ..bcrypt import bcrypt

        errors = {}
        if len(email.strip()) == 0:
            errors["email"] = ["email cannot be empty"]
        if len(password.strip()) == 0:
            errors["password"] = ["password cannot be empty"]
        if errors:
            return jsonify({"message": "input invalid", "errors": errors}), 400
        if not (user := await UserDatabase.get("email", email=email)):
            return jsonify({"message": "failed login"}), 404
        if not bcrypt.check_password_hash(user.password, password):
            return (
                jsonify({"message": "authorization invalid", "data": {"email": email}}),
                401,
            )
        created_at = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if not user.is_active:
            expired_at = created_at + 300
            email_token = await TokenAccountActiveEmail.insert(
                f"{user.user_id}", int(created_at)
            )
            web_token = await TokenAccountActiveWeb.insert(
                f"{user.user_id}", int(created_at)
            )
            user_token = await AccountActiveDatabase.insert(
                generate_id(),
                f"{user.user_id}",
                email_token,
                web_token,
                int(expired_at),
                created_at,
            )
            send_email_task.apply_async(
                args=[
                    "Account Active",
                    [user.email],
                    f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Password Reset</title>
</head>
<body>
    <p>Hello {user.username},</p>
    <p>Someone has requested a link to verify your account, and you can do this through the link below.</p>
    <p>
        <a href="{job_entry_url}/account-active?token={email_token}">
            Click here to activate your account
        </a>
    </p>
    <p>If you didn't request this, please ignore this email.</p>
</body>
</html>
                    """,
                    "account active",
                ],
            )
            return (
                jsonify(
                    {
                        "message": "user inactive",
                        "data": {
                            "user_id": user.user_id,
                            "username": user.username,
                            "email": user.email,
                            "is_active": user.is_active,
                        },
                        "verification": {
                            "token": user_token.token_web,
                            "created_at": user_token.created_at,
                            "updated_at": user_token.updated_at,
                        },
                    }
                ),
                403,
            )
        access_token = create_access_token(identity=user)
        return (
            jsonify(
                {
                    "message": "success login",
                    "data": {
                        "user_id": user.user_id,
                        "username": user.username,
                        "email": user.email,
                        "access_token": access_token,
                        "is_active": user.is_active,
                    },
                }
            ),
            201,
        )

    @staticmethod
    async def user_register(email, username, password, avatar):
        from ..bcrypt import bcrypt

        errors = {}
        if len(email.strip()) == 0:
            if "email" in errors:
                errors["email"].append("email cant be empety")
            else:
                errors["email"] = ["email cant be empety"]
        if len(username.strip()) == 0:
            if "username" in errors:
                errors["username"].append("username cant be empety")
            else:
                errors["username"] = ["username cant be empety"]
        if len(password.strip()) == 0:
            if "password" in errors:
                errors["password"].append("password cant be empety")
            else:
                errors["password"] = ["password cant be empety"]
        if len(password) < 8:
            if "password" in errors:
                errors["password"].append("minimum 8 characters")
            else:
                errors["password"] = ["minimum 8 characters"]
        if not re.search("[a-z]", password):
            if "password" in errors:
                errors["password"].append("password must contain lowercase")
            else:
                errors["password"] = ["password must contain lowercase"]
        if not re.search("[A-Z]", password):
            if "password" in errors:
                errors["password"].append("password must contain uppercase")
            else:
                errors["password"] = ["password must contain uppercase"]
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            if "password" in errors:
                errors["password"].append("password contains special character(s)")
            else:
                errors["password"] = ["password contains special character(s)"]
        if len(username) > 20:
            if "username" in errors:
                errors["username"].append("username must be less than 20 characters")
            else:
                errors["username"] = ["username must be less than 20 characters"]
        if len(email) > 50:
            if "email" in errors:
                errors["email"].append("email must be less than 50 characters")
            else:
                errors["email"] = ["email must be less than 50 characters"]
        if errors:
            return (
                jsonify(
                    {
                        "message": "input invalid",
                        "errors": errors,
                    }
                ),
                400,
            )
        result_password = bcrypt.generate_password_hash(password).decode("utf-8")
        user_id = generate_id()
        avatar_id = generate_id()
        created_at = datetime.datetime.now(datetime.timezone.utc).timestamp()
        try:
            user = await UserDatabase.insert(
                user_id, email, username, result_password, avatar_id, avatar, created_at
            )
        except sqlalchemy.exc.IntegrityError:
            return (
                jsonify(
                    {
                        "message": "username or email already exists",
                        "data": {"username": username, "email": email},
                    },
                ),
                409,
            )
        expired_at = created_at + 300
        email_token = await TokenAccountActiveEmail.insert(
            f"{user.user_id}", int(created_at)
        )
        web_token = await TokenAccountActiveWeb.insert(
            f"{user.user_id}", int(created_at)
        )
        user_token = await AccountActiveDatabase.insert(
            generate_id(),
            f"{user.user_id}",
            email_token,
            web_token,
            int(expired_at),
            created_at,
        )
        send_email_task.apply_async(
            args=[
                "Account Active",
                [user.email],
                f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Password Reset</title>
</head>
<body>
    <p>Hello {user.username},</p>
    <p>Someone has requested a link to verify your account, and you can do this through the link below.</p>
    <p>
        <a href="{job_entry_url}/account-active?token={email_token}">
            Click here to activate your account
        </a>
    </p>
    <p>If you didn't request this, please ignore this email.</p>
</body>
</html>
                """,
                "account active",
            ],
        )
        return (
            jsonify(
                {
                    "message": "success register",
                    "data": {
                        "username": user.username,
                        "email": user.email,
                        "is_active": user.is_active,
                        "updated_at": user.updated_at,
                        "user_id": user.user_id,
                        "created_at": user.created_at,
                    },
                    "verification": {
                        "token": user_token.token_web,
                        "created_at": user_token.created_at,
                        "updated_at": user_token.updated_at,
                    },
                }
            ),
            201,
        )
