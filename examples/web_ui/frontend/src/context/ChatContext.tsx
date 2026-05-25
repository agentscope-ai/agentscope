import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

interface ChatContextValue {
	/** Currently selected agent ID, or null if none selected. */
	selectedAgentId: string | null;
	setSelectedAgentId: (id: string | null) => void;
	/** Currently selected session ID, or null if none selected. */
	selectedSessionId: string | null;
	setSelectedSessionId: (id: string | null) => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

/**
 * Provides shared chat state (selected agent and session) to the component tree.
 * Wrap the chat page root with this provider.
 */
export function ChatProvider({ children }: { children: ReactNode }) {
	const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
	const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

	return (
		<ChatContext.Provider
			value={{
				selectedAgentId,
				setSelectedAgentId,
				selectedSessionId,
				setSelectedSessionId,
			}}
		>
			{children}
		</ChatContext.Provider>
	);
}

/**
 * Consumes ChatContext. Must be called inside a ChatProvider.
 * @throws if used outside of ChatProvider
 */
export function useChatContext() {
	const ctx = useContext(ChatContext);
	if (!ctx) throw new Error('useChatContext must be used within <ChatProvider>');
	return ctx;
}
