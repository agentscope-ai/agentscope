import { useEffect, useState } from 'react';

import { commandApi, type CommandInfo } from '@/api';

let cachedCommands: CommandInfo[] | null = null;

export function useCommands() {
	const [commands, setCommands] = useState<CommandInfo[]>(cachedCommands ?? []);

	useEffect(() => {
		if (cachedCommands !== null) return;
		commandApi
			.list()
			.then(({ commands: next }) => {
				cachedCommands = next;
				setCommands(next);
			})
			.catch(() => {
				cachedCommands = [];
			});
	}, []);

	return commands;
}
