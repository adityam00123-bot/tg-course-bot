"""
Classplus LMS API Extractor.
Extracts course hierarchy, clean raw HLS/m3u8 video streams, and PDF study notes.
"""

import logging
import aiohttp
from typing import Dict, Any, List, Optional

logger = logging.getLogger("classplus_extractor")


class ClassplusAPI:
    """Classplus LMS API Handler."""

    BASE_URL = "https://api.classplusapp.com"

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.headers = {
            "api-version": "45",
            "region": "IN",
            "accept": "application/json, text/plain, */*",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "device-id": "550e8400-e29b-41d4-a716-446655440000"
        }
        if self.token:
            self.headers["x-access-token"] = self.token

    def set_token(self, token: str):
        """Update active student auth token."""
        self.token = token.strip()
        self.headers["x-access-token"] = self.token

    async def verify_org_code(self, org_code: str) -> Dict[str, Any]:
        """Verify organization code and retrieve orgId and details."""
        url = f"{self.BASE_URL}/v2/orgs/check?orgCode={org_code.strip()}"
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("status") == "success":
                    org_data = data.get("data", {})
                    return {
                        "success": True,
                        "org_id": org_data.get("orgId"),
                        "org_name": org_data.get("orgName") or org_code.upper()
                    }
                return {"success": False, "error": data.get("message", "Invalid Org Code")}

    async def send_otp(self, mobile: str, org_id: int) -> Dict[str, Any]:
        """Generate and send login OTP to the student's mobile number."""
        url = f"{self.BASE_URL}/v2/otp/generate"
        payload = {
            "mobile": mobile.strip(),
            "orgId": org_id,
            "viaSms": 1
        }
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("status") == "success":
                    return {
                        "success": True,
                        "session_id": data.get("data", {}).get("sessionId")
                    }
                return {"success": False, "error": data.get("message", "Failed to send OTP")}

    async def verify_otp(self, mobile: str, otp: str, org_id: int, session_id: str) -> Dict[str, Any]:
        """Verify OTP and extract student access JWT token."""
        url = f"{self.BASE_URL}/v2/users/login"
        payload = {
            "mobile": mobile.strip(),
            "otp": otp.strip(),
            "orgId": org_id,
            "sessionId": session_id,
            "fingerprintId": "550e8400-e29b-41d4-a716-446655440000"
        }
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("status") == "success":
                    token = data.get("data", {}).get("token")
                    user = data.get("data", {}).get("user", {})
                    self.set_token(token)
                    return {
                        "success": True,
                        "token": token,
                        "user_name": user.get("name", "Student"),
                        "user_id": user.get("id")
                    }
                return {"success": False, "error": data.get("message", "Invalid OTP")}

    async def get_enrolled_courses(self) -> List[Dict[str, Any]]:
        """Fetch all purchased / enrolled store courses."""
        if not self.token:
            raise ValueError("Authentication token required.")

        url = f"{self.BASE_URL}/v2/courses/my-courses"
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                courses = []
                if resp.status == 200 and data.get("status") == "success":
                    raw_list = data.get("data", {}).get("courses", []) or []
                    for c in raw_list:
                        courses.append({
                            "id": c.get("id"),
                            "title": c.get("name") or c.get("title", "Untitled Course"),
                            "thumbnail": c.get("imageUrl") or c.get("thumbnail"),
                            "type": "course"
                        })
                return courses

    async def get_enrolled_batches(self) -> List[Dict[str, Any]]:
        """Fetch all enrolled batch classrooms."""
        if not self.token:
            raise ValueError("Authentication token required.")

        url = f"{self.BASE_URL}/v2/batches/list"
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                batches = []
                if resp.status == 200 and data.get("status") == "success":
                    raw_list = data.get("data", {}).get("batches", []) or []
                    for b in raw_list:
                        batches.append({
                            "id": b.get("id"),
                            "title": b.get("name") or b.get("batchName", "Untitled Batch"),
                            "thumbnail": b.get("imageUrl"),
                            "type": "batch"
                        })
                return batches

    async def get_folder_contents(self, course_id: int, folder_id: int = 0) -> Dict[str, Any]:
        """
        Recursively traverse course content hierarchy:
        Returns:
            - folders: List of subfolders
            - videos: List of clean video stream details (m3u8, direct mp4)
            - pdfs: List of study material documents
        """
        if not self.token:
            raise ValueError("Authentication token required.")

        url = f"{self.BASE_URL}/v2/course/content/get?courseId={course_id}&folderId={folder_id}"
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                folders = []
                videos = []
                pdfs = []

                if resp.status == 200 and data.get("status") == "success":
                    contents = data.get("data", {}).get("courseContent", []) or []
                    for item in contents:
                        content_type = item.get("contentType")
                        name = item.get("name") or item.get("title", "Untitled")

                        # 1 = Folder / Topic
                        if content_type == 1 or item.get("type") == "folder":
                            folders.append({
                                "id": item.get("id"),
                                "name": name,
                                "course_id": course_id
                            })
                        # 2 = Video Lecture
                        elif content_type == 2 or item.get("type") == "video":
                            stream_url = item.get("url") or item.get("videoUrl") or item.get("encryptedUrl")
                            videos.append({
                                "id": item.get("id"),
                                "name": name,
                                "url": stream_url,
                                "duration": item.get("duration", 0),
                                "thumbnail": item.get("thumbnailUrl") or item.get("imageUrl"),
                                "is_encrypted": bool(item.get("isEncrypted", 0))
                            })
                        # 3 = PDF / Document Material
                        elif content_type == 3 or item.get("type") == "document" or str(name).lower().endswith(".pdf"):
                            pdf_url = item.get("url") or item.get("documentUrl")
                            pdfs.append({
                                "id": item.get("id"),
                                "name": name if name.lower().endswith(".pdf") else f"{name}.pdf",
                                "url": pdf_url
                            })

                return {
                    "folders": folders,
                    "videos": videos,
                    "pdfs": pdfs
                }
