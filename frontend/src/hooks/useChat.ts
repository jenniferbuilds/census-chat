/**
 * Chat state management hook.
 * Handles message list, session ID, streaming state, and agent invocation.
 */
import { useState, useCallback, useRef } from 'react'
import { v4 as uuidv4 } from 'uuid'
import { invokeAgent } from '../lib/agentcore'
import { AuthUser } from '../lib/auth'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'error'
  content: string
  timestamp: Date
}

export function useChat(user: AuthUser | null) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        "Hello! I'm your US Census data assistant. I can answer questions about " +
        'population, demographics, income, housing, employment, and education ' +
        'statistics across US states and counties. What would you like to explore?',
      timestamp: new Date(),
    },
  ])
  const [isLoading, setIsLoading] = useState(false)
  const sessionIdRef = useRef<string>(uuidv4())

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim() || isLoading || !user) return

      const userMsg: Message = {
        id: uuidv4(),
        role: 'user',
        content: content.trim(),
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, userMsg])
      setIsLoading(true)

      try {
        const responseText = await invokeAgent({
          message: content.trim(),
          actorId: user.userId,
          sessionId: sessionIdRef.current,
          accessToken: user.accessToken,
        })

        const assistantMsg: Message = {
          id: uuidv4(),
          role: 'assistant',
          content: responseText,
          timestamp: new Date(),
        }
        setMessages((prev) => [...prev, assistantMsg])
      } catch (err: unknown) {
        const errorMsg: Message = {
          id: uuidv4(),
          role: 'error',
          content:
            err instanceof Error
              ? err.message
              : 'Something went wrong. Please try again.',
          timestamp: new Date(),
        }
        setMessages((prev) => [...prev, errorMsg])
      } finally {
        setIsLoading(false)
      }
    },
    [isLoading, user]
  )

  const clearChat = useCallback(() => {
    sessionIdRef.current = uuidv4() // New session = new context
    setMessages([
      {
        id: 'welcome',
        role: 'assistant',
        content:
          "Hello! I'm your US Census data assistant. What would you like to explore?",
        timestamp: new Date(),
      },
    ])
  }, [])

  return { messages, isLoading, sendMessage, clearChat }
}
