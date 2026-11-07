import React from 'react'
import ReactMarkdown from 'react-markdown'
import { Message } from '../hooks/useChat'
import { BarChart3, User, AlertCircle } from 'lucide-react'

interface MessageBubbleProps {
  message: Message
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user'
  const isError = message.role === 'error'

  if (isUser) {
    return (
      <div className="flex justify-end gap-3 mb-4">
        <div className="max-w-[75%] bg-brand-600 text-white px-4 py-3 rounded-2xl rounded-tr-sm shadow-sm">
          <p className="text-sm leading-relaxed">{message.content}</p>
        </div>
        <div className="w-8 h-8 bg-gray-200 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5">
          <User size={16} className="text-gray-600" />
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex justify-start gap-3 mb-4">
        <div className="w-8 h-8 bg-red-100 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5">
          <AlertCircle size={16} className="text-red-500" />
        </div>
        <div className="max-w-[75%] bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-2xl rounded-tl-sm shadow-sm">
          <p className="text-sm leading-relaxed">{message.content}</p>
        </div>
      </div>
    )
  }

  // Assistant message
  return (
    <div className="flex justify-start gap-3 mb-4">
      <div className="w-8 h-8 bg-brand-600 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5">
        <BarChart3 size={15} className="text-white" />
      </div>
      <div className="max-w-[80%] bg-white border border-gray-200 px-4 py-3 rounded-2xl rounded-tl-sm shadow-sm">
        <div className="text-sm leading-relaxed text-gray-800 prose-chat">
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>
        <p className="text-xs text-gray-400 mt-2">
          {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </p>
      </div>
    </div>
  )
}
