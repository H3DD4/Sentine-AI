from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import get_current_active_user
from app.db import get_session
from app.models import AnalysisConversation, User
from app.schemas import ConversationOut, ConversationState

router = APIRouter(prefix="/conversations", tags=["conversations"])

def _owned(query, user_id: str):
    return query.where(AnalysisConversation.user_id == user_id)

@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    result = await session.execute(
        _owned(select(AnalysisConversation), user.id)
        .order_by(AnalysisConversation.updated_at.desc()).limit(100)
    )
    return result.scalars().all()

@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(
    state: ConversationState,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    item = AnalysisConversation(user_id=user.id, **state.model_dump(exclude_none=True))
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item

@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    item = await session.scalar(_owned(select(AnalysisConversation).where(AnalysisConversation.id == conversation_id), user.id))
    if not item:
        raise HTTPException(404, "Conversation not found")
    return item

@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: str,
    state: ConversationState,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    item = await session.scalar(_owned(select(AnalysisConversation).where(AnalysisConversation.id == conversation_id), user.id))
    if not item:
        raise HTTPException(404, "Conversation not found")
    for key, value in state.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return item

@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    item = await session.scalar(_owned(select(AnalysisConversation).where(AnalysisConversation.id == conversation_id), user.id))
    if not item:
        raise HTTPException(404, "Conversation not found")
    await session.delete(item)
    await session.commit()
