import { useState, useEffect, useCallback, useRef } from 'react';

import { workspaceApi } from '@/api';
import type { MCPClient, MCPClientStatus, Skill } from '@/api';
import { ApiError } from '@/api/client';
import type { UploadOptions } from '@/api/workspace';

export function useWorkspace(agentId: string | null, sessionId: string | null) {
	const [mcps, setMcps] = useState<MCPClientStatus[]>([]);
	const [skills, setSkills] = useState<Skill[]>([]);
	const [loading, setLoading] = useState(false);
	const [refreshing, setRefreshing] = useState(false);
	const [skillsLoading, setSkillsLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const mcpRequestGeneration = useRef(0);

	const fetchMcps = useCallback(
		async (generation: number, initial: boolean) => {
			if (!agentId || !sessionId) {
				return;
			}
			if (initial) setLoading(true);
			else setRefreshing(true);
			setError(null);
			try {
				const result = await workspaceApi.mcp.listForManagement(agentId, sessionId);
				if (generation === mcpRequestGeneration.current) setMcps(result);
			} catch (e) {
				if (generation === mcpRequestGeneration.current) setError(e as Error);
			} finally {
				if (generation === mcpRequestGeneration.current) {
					setLoading(false);
					setRefreshing(false);
				}
			}
		},
		[agentId, sessionId],
	);

	const refetch = useCallback(async () => {
		const generation = ++mcpRequestGeneration.current;
		await fetchMcps(generation, false);
	}, [fetchMcps]);

	const refetchSkills = useCallback(async () => {
		if (!agentId || !sessionId) {
			setSkills([]);
			return;
		}
		setSkillsLoading(true);
		try {
			setSkills(await workspaceApi.skill.list(agentId, sessionId));
		} catch (e) {
			setError(e as Error);
		} finally {
			setSkillsLoading(false);
		}
	}, [agentId, sessionId]);

	useEffect(() => {
		const generation = ++mcpRequestGeneration.current;
		setMcps([]);
		setRefreshing(false);
		if (!agentId || !sessionId) {
			setLoading(false);
			return;
		}
		setLoading(true);
		const timer = window.setTimeout(() => {
			void fetchMcps(generation, true);
		}, 300);
		return () => window.clearTimeout(timer);
	}, [agentId, sessionId, fetchMcps]);
	useEffect(() => {
		refetchSkills();
	}, [refetchSkills]);

	const addMcps = useCallback(
		async (clients: MCPClient[]) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			const existingNames = new Set(mcps.map((m) => m.name));
			for (const mcp of clients) {
				if (existingNames.has(mcp.name)) {
					throw new Error(`MCP server "${mcp.name}" already exists in this workspace.`);
				}
			}
			const batchNames = new Set<string>();
			for (const mcp of clients) {
				if (batchNames.has(mcp.name)) {
					throw new Error(`Duplicate MCP server name "${mcp.name}" in configuration.`);
				}
				batchNames.add(mcp.name);
			}
			for (const mcp of clients) {
				await workspaceApi.mcp.add(agentId, sessionId, mcp);
			}
			await refetch();
		},
		[agentId, sessionId, mcps, refetch],
	);

	const addMcpsFromLibrary = useCallback(
		async (mcpIds: string[]) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			const result = await workspaceApi.mcp.addFromLibrary(agentId, sessionId, mcpIds);
			await refetch();
			// Reported per MCP, so a partial success is still a success for
			// what landed; surface only what did not.
			const failures = Object.entries(result.failed);
			if (failures.length > 0) {
				throw new Error(failures.map(([name, why]) => `${name}: ${why}`).join('\n'));
			}
		},
		[agentId, sessionId, refetch],
	);

	const removeMcp = useCallback(
		async (mcpName: string) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			await workspaceApi.mcp.remove(mcpName, agentId, sessionId);
			await refetch();
		},
		[agentId, sessionId, refetch],
	);

	const setMcpToolEnabled = useCallback(
		async (mcpName: string, toolName: string, enabled: boolean) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			const generation = mcpRequestGeneration.current;
			const previous = mcps
				.find((mcp) => mcp.name === mcpName)
				?.tools.find((tool) => tool.raw_name === toolName)?.enabled;
			setMcps((current) =>
				current.map((mcp) => {
					if (mcp.name !== mcpName) return mcp;
					return {
						...mcp,
						tools: mcp.tools.map((tool) => {
							if (tool.raw_name !== toolName) return tool;
							return { ...tool, enabled };
						}),
					};
				}),
			);

			try {
				await workspaceApi.mcp.setToolEnabled(
					agentId,
					sessionId,
					mcpName,
					toolName,
					enabled,
				);
			} catch (e) {
				if (generation === mcpRequestGeneration.current && previous !== undefined) {
					setMcps((current) =>
						current.map((mcp) =>
							mcp.name !== mcpName
								? mcp
								: {
										...mcp,
										tools: mcp.tools.map((tool) =>
											tool.raw_name === toolName
												? { ...tool, enabled: previous }
												: tool,
										),
									},
						),
					);
					if (e instanceof ApiError && (e.status === 404 || e.status === 409)) {
						await refetch();
					}
				}
				throw e;
			}
		},
		[agentId, sessionId, mcps, refetch],
	);

	const uploadSkill = useCallback(
		async (files: File[], options: UploadOptions = {}) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			await workspaceApi.skill.upload(agentId, sessionId, files, options);
			await refetchSkills();
		},
		[agentId, sessionId, refetchSkills],
	);

	const addSkillsFromLibrary = useCallback(
		async (skillIds: string[]) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			const result = await workspaceApi.skill.addFromLibrary(agentId, sessionId, skillIds);
			await refetchSkills();
			// Reported per skill, so a partial success is still a success
			// for what landed; surface only what did not.
			const failures = Object.entries(result.failed);
			if (failures.length > 0) {
				throw new Error(failures.map(([name, why]) => `${name}: ${why}`).join('\n'));
			}
		},
		[agentId, sessionId, refetchSkills],
	);

	const removeSkill = useCallback(
		async (skillName: string) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			await workspaceApi.skill.remove(skillName, agentId, sessionId);
			await refetchSkills();
		},
		[agentId, sessionId, refetchSkills],
	);

	return {
		mcps,
		loading,
		refreshing,
		error,
		refetch,
		addMcps,
		addMcpsFromLibrary,
		removeMcp,
		setMcpToolEnabled,
		skills,
		skillsLoading,
		uploadSkill,
		addSkillsFromLibrary,
		removeSkill,
	};
}
