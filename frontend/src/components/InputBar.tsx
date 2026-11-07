import React, { useState, useRef, useEffect } from 'react'
import { Send, Loader2 } from 'lucide-react'

interface InputBarProps {
  onSend: (message: string) => void
  isLoading: boolean
}

const SUGGESTIONS = [
  'What is the median income in Cook County, Illinois?',
  'Compare poverty rates between California and Mississippi',
  'What percentage of Los Angeles County is Hispanic?',
  'Which states have the highest homeownership rates?',
]

export default function InputBar({ onSend, isLoading }: InputBarProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`
    }
  }, [value])

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!value.trim() || isLoading) return
    onSend(value)
    setValue('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="border-t border-gray-200 bg-white px-4 py-4">
      {/* Suggestion chips (only when empty) */}
      {!value && !isLoading && (
        <div className="flex flex-wrap gap-2 mb-3">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => onSend(s)}
              className="text-xs px-3 py-1.5 bg-gray-100 hover:bg-brand-100 hover:text-brand-700 text-gray-600 rounded-full transition-colors truncate max-w-xs"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex items-end gap-3">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about US Census data…"
          rows={1}
          disabled={isLoading}
          className="flex-1 resize-none px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none transition text-sm placeholder-gray-400 disabled:bg-gray-50"
        />
        <button
          type="submit"
          disabled={!value.trim() || isLoading}
          className="w-11 h-11 flex items-center justify-center bg-brand-600 hover:bg-brand-700 disabled:bg-gray-300 rounded-xl transition-colors flex-shrink-0"
          aria-label="Send"
        >
          {isLoading ? (
            <Loader2 size={18} className="text-white animate-spin" />
          ) : (
            <Send size={18} className="text-white" />
          )}
        </button>
      </form>
      <p className="text-xs text-gray-400 mt-2 text-center">
        Shift+Enter for new line · Data: ACS 2020 5-Year Estimates
      </p>
    </div>
  )
}
