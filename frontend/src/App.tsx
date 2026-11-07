import React, { useState, useEffect } from 'react'
import { AuthUser, getCurrentUser, signOut } from './lib/auth'
import Login from './components/Login'
import ChatWindow from './components/ChatWindow'
import InputBar from './components/InputBar'
import { useChat } from './hooks/useChat'
import { LogOut, Trash2, BarChart3 } from 'lucide-react'

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [authLoading, setAuthLoading] = useState(true)

  // Restore session on mount
  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .finally(() => setAuthLoading(false))
  }, [])

  const { messages, isLoading, sendMessage, clearChat } = useChat(user)

  const handleLogout = () => {
    signOut()
    setUser(null)
  }

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!user) {
    return <Login onLogin={setUser} />
  }

  return (
    <div className="flex flex-col h-screen max-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center gap-3 flex-shrink-0">
        <div className="w-8 h-8 bg-brand-600 rounded-lg flex items-center justify-center">
          <BarChart3 size={16} className="text-white" />
        </div>
        <div className="flex-1">
          <h1 className="text-base font-semibold text-gray-900">Census Chat</h1>
          <p className="text-xs text-gray-500">US Population Data · ACS 2020</p>
        </div>
        <div className="flex items-center gap-1">
          <span className="text-xs text-gray-500 mr-2">
            {user.username}
          </span>
          <button
            onClick={clearChat}
            title="New conversation"
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors text-gray-500"
          >
            <Trash2 size={16} />
          </button>
          <button
            onClick={handleLogout}
            title="Sign out"
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors text-gray-500"
          >
            <LogOut size={16} />
          </button>
        </div>
      </header>

      {/* Chat area */}
      <ChatWindow messages={messages} isLoading={isLoading} />

      {/* Input */}
      <InputBar onSend={sendMessage} isLoading={isLoading} />
    </div>
  )
}
