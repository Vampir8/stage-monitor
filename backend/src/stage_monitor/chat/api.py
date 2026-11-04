from fastapi import Depends, Query

from .models import MessageCreate, ReadMarker, RoomCreate, Typing


def register(app, current_user):
    @app.get("/api/chat/rooms")
    async def rooms(user=Depends(current_user)):
        return await app.state.chat.list_rooms(user)

    @app.post("/api/chat/rooms", status_code=201)
    async def create_room(data: RoomCreate, user=Depends(current_user)):
        return await app.state.chat.create_room(data, user)

    @app.get("/api/chat/users")
    async def users(user=Depends(current_user)):
        return await app.state.chat.users()

    @app.get("/api/chat/rooms/{room_id}/messages")
    async def messages(
        room_id: str,
        before: int | None = Query(None, ge=1),
        after: int | None = Query(None, ge=0),
        limit: int = Query(50, ge=1, le=100),
        user=Depends(current_user),
    ):
        if before is not None and after is not None:
            from fastapi import HTTPException

            raise HTTPException(422, "Use before or after, not both")
        return await app.state.chat.messages(room_id, user, before, after, limit)

    @app.post("/api/chat/rooms/{room_id}/messages", status_code=201)
    async def send_message(room_id: str, data: MessageCreate, user=Depends(current_user)):
        return await app.state.chat.send(room_id, data, user)

    @app.post("/api/chat/rooms/{room_id}/read")
    async def read(room_id: str, data: ReadMarker, user=Depends(current_user)):
        await app.state.chat.mark_read(room_id, data.message_id, user)
        return {"status": "ok"}

    @app.post("/api/chat/rooms/{room_id}/typing")
    async def typing(room_id: str, data: Typing, user=Depends(current_user)):
        app.state.chat.set_typing(room_id, user, data.typing)
        return {"status": "ok"}
