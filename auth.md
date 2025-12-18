# Authentication API Documentation

This document describes all authentication-related endpoints including username/password login, OAuth (Google + GitHub), signup, account linking, and token issuance.

Base router prefix: **`/auth`**

---

# Authentication Flow Overview

There are two authentication modes:

---

# 1. Local Account (OTP Required for Signup)

## Username + Password Login

### **POST /auth/token**

Authenticate with form data (`application/x-www-form-urlencoded`).

Form Fields:

| Field     | Type   |
|-----------|--------|
| username  | string |
| password  | string |

### Response 200
```
{
  "access_token": "jwt",
  "token_type": "bearer"
}
```

### Errors
- `401 Unauthorized` — invalid username or password.

---

## Request OTP Code

### **GET /auth/otp/get-code/**

Generates and sends a one-time passcode to the provided email.

OTP is used for:
- Email verification during signup
- Changing password (authenticated)
- Forgot password (unauthenticated)

OTP codes:
- Are numeric
- Length: 6 digits
- Stored in Redis
- Have resend cooldown (`CODE_RESEND_SECONDS`)
- Are invalidated after successful verification

**Auth required:** No  

**Query Parameters**

| Name  | Type  | Required |
|------|-------|----------|
| email | Email | Yes |

### Behavior
- If a valid OTP was sent recently → request is rejected
- Otherwise:
  - Generate 6-digit numeric OTP
  - Store it in Redis
  - Send via email (AWS SES)
  - Return code (temporary dev behavior)

### Response 200

```
{
  "detail": "OTP code generated and sent.",
  "code": "123456"
}
```

### Errors
- `400 A code has already been sent recently.`

---

## Create User (OTP Verified)

### **POST /auth/create_user/**

Creates a new local user account after OTP verification.

**Auth required:** No  

### Request Body — UserRequest (extended)

| Field        | Type   | Required |
|-------------|--------|----------|
| username    | string | Yes |
| email       | string | Yes |
| password    | string | Yes |
| otp         | string | Yes |
| first_name  | string | No |
| last_name   | string | No |
| phone_number| string | No |

Example:

```
{
  "username": "newuser",
  "email": "newuser@example.com",
  "password": "strongpassword",
  "otp": "123456"
}
```

### Flow
1. Verify OTP against Redis
2. Check username uniqueness
3. Check email uniqueness
4. Create user with hashed password

### Response 201

```
"User Created"
```

### Errors
- `400 Username already taken`
- `400 E-mail already taken`
- `400 Invalid or expired OTP`

---

## Change Password (Authenticated)

### **POST /auth/change-password**

Allows a logged-in user to change their password using OTP verification.

**Auth required:** Yes  

### Headers

```
Authorization: Bearer <access_token>
```

### Request Body — ChangePasswordRequest

| Field        | Type   | Required |
|-------------|--------|----------|
| new_password| string | Yes |
| otp         | string | Yes |
| old_password| string | Conditional |

- `old_password` is required **if** user already has a password

Example:

```
{
  "old_password": "oldpassword",
  "new_password": "newpassword",
  "otp": "123456"
}
```

### Flow
1. Validate access token
2. Verify OTP for user email
3. Verify old password (if exists)
4. Hash and update password

### Response 202

```
"Password Changed"
```

### Errors
- `401 Authentication Failed`
- `401 Old password did not match`
- `400 Old password is required`
- `400 Invalid or expired OTP`

---

## Forgot Password (Unauthenticated)

### **POST /auth/forget-password**

Resets password using OTP without requiring login.

**Auth required:** No  

### Query Parameters

| Name  | Type  | Required |
|------|-------|----------|
| email | Email | Yes |

### Request Body — ChangePasswordRequest

| Field        | Type   | Required |
|-------------|--------|----------|
| new_password| string | Yes |
| otp         | string | Yes |

Example:

```
{
  "new_password": "newpassword",
  "otp": "123456"
}
```

### Flow
1. Locate user by email
2. Verify OTP
3. Replace password with hashed value

### Response 202

```
"Password Changed"
```

### Errors
- `404 User not found`
- `400 Invalid or expired OTP`

---

# 2. OAuth Authentication (Google + GitHub)

OAuth uses **redirects**, a **pending token**, and multiple states:

- **existing user linked → issue token**
- **OAuth account belongs to another user → reject**
- **email exists but OAuth not linked → require password to bind**
- **email does not exist → require username to complete signup**

Frontend receives callback with URL parameters specifying the flow.

---

## Google OAuth

### **GET /auth/google/login**

Redirects user to Google login.

### **GET /auth/google/callback**

Google returns:

- email
- provider_id (`sub`)
- profile name

Backend determines if:

1. This Google account is already linked to a local user → **login immediately**
2. Email exists but Google not linked → **require account binding**
3. New user → **require username selection**

Returned redirect URL includes parameters such as:

```
/auth/google?status=new_user&pending_token=...
```

---

## GitHub OAuth

### **GET /auth/github/login**

Redirect to GitHub authorization.

### **GET /auth/github/callback**

GitHub returns:

- GitHub ID
- login (username)
- email (may require secondary `/user/emails` request)

Flow is the same as Google.

---

# Internal OAuth States

The backend sets:

```py
kind: "oauth_pending"
mode: "signup" | "link"
provider: "google" | "github"
provider_id: string
email: optional string
exp: timestamp
```


Returned to frontend via:  
`http://localhost:3000/oauth/{provider}?status=...`

---

# Post-OAuth Endpoints

## Complete OAuth Signup

### **POST /auth/complete-signup**  
(Request Body: CompleteSignupBody)

Used when OAuth user has no existing account and must create one by choosing username.

### Response 200
```
{
  "access_token": "...",
  "token_type": "bearer"
}
```

### Errors
- `400 Invalid or expired token`
- `400 Username already taken`

---

## Bind Existing Account to OAuth Provider

### **POST /auth/bind-account**  
(Request Body: BindAccountBody)

Used when:

- User logs in with OAuth
- Their email already exists
- Must verify password to link OAuth provider

### Response 200
```
{
  "access_token": "...",
  "token_type": "bearer"
}
```

### Errors
- `400 Invalid or expired token`
- `401 Incorrect password`
- `404 User not found`
- `400 Social account already linked`

---

# Login Logic Summary

**1. User logs in with OAuth provider**  
→ Backend identifies user or returns a `pending_token`.

**2. Pending Token "mode" determines next step:**

### `"mode": "signup"`
User must select a username → `/auth/complete-signup`

### `"mode": "link"`
User must verify password → `/auth/bind-account`

### No pending:  
User is fully authenticated → Frontend receives:

```
?status=logged_in&access_token=...
```

---

# Helper Endpoints (internal usage)

These endpoints are used indirectly in OAuth redirection flows:

---

### **GET /auth/google/login**
Starts Google OAuth.

### **GET /auth/github/login**
Starts GitHub OAuth.

### **GET /auth/google/callback**
Processes Google callback.

### **GET /auth/github/callback**
Processes GitHub callback.

These do not return JSON — they redirect to the frontend.

---

# Token Format

Issued JWT structure:

```
{
  "sub": "username",
  "id": user_id,
  "role": "user" | "admin",
  "exp": "<timestamp>"
}
```

Algorithm: **HS256**  
Expiration: **20 minutes** (local accounts), **10 minutes** (OAuth pending tokens)

---

# Protected Routes

Most other API routes require:

```
Authorization: Bearer <access_token>
```

And depend on:

```
Depends(oauth2_bearer)
```

which uses `/auth/token` by default.

---

# End of Authentication API Markdown
