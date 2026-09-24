import { client } from './client';
import type {
	HubBrowseParams,
	HubInfo,
	InstallMCPRequest,
	MCPCard,
	MCPHubPage,
	MCPView,
	SkillCard,
	SkillHubPage,
	SkillView,
} from './types';

/**
 * Card ids are opaque and may contain characters that are not path-safe —
 * ClawHub's search endpoint, for one, returns ids containing `:`.
 */
const segment = (value: string) => encodeURIComponent(value);

/** Drop empty filters so the backend applies its own defaults. */
function browseQuery(params?: HubBrowseParams): Record<string, string> {
	const query: Record<string, string> = {};
	if (params?.q) query.q = params.q;
	if (params?.cursor) query.cursor = params.cursor;
	if (params?.limit !== undefined) query.limit = String(params.limit);
	return query;
}

/**
 * Resource hubs. Browsing is three levels deep — pick a hub, browse its
 * cards, install one. Cards are never merged across hubs, so each hub
 * paginates on its own.
 */
export const hubApi = {
	mcp: {
		listHubs: () => client.get<HubInfo[]>('/hub/mcp'),

		listCards: (hubId: string, params?: HubBrowseParams) =>
			client.get<MCPHubPage>(`/hub/mcp/${segment(hubId)}/cards`, browseQuery(params)),

		getCard: (hubId: string, cardId: string) =>
			client.get<MCPCard>(`/hub/mcp/${segment(hubId)}/cards/${segment(cardId)}`),

		/**
		 * Renders the card's template with `body.values` into the user's
		 * library. No workspace is involved — putting the MCP into a session
		 * is a separate act. The config is not connection-tested, so a wrong
		 * API key surfaces on first use, not here. A 409 means the name is
		 * taken — retry with `body.name` set.
		 */
		install: (
			hubId: string,
			cardId: string,
			body: InstallMCPRequest,
			options?: { silent?: boolean },
		) =>
			client.post<MCPView>(
				`/hub/mcp/${segment(hubId)}/cards/${segment(cardId)}/install`,
				body,
				undefined,
				options,
			),
	},

	skill: {
		listHubs: () => client.get<HubInfo[]>('/hub/skill'),

		listCards: (hubId: string, params?: HubBrowseParams) =>
			client.get<SkillHubPage>(`/hub/skill/${segment(hubId)}/cards`, browseQuery(params)),

		/** Unlike the list endpoint, this also fetches the `SKILL.md` body. */
		getCard: (hubId: string, cardId: string) =>
			client.get<SkillCard>(`/hub/skill/${segment(hubId)}/cards/${segment(cardId)}`),

		/**
		 * Records the card in the user's library. Like the MCP install this
		 * touches no workspace, and it does not download the archive — that
		 * happens when the skill is put into a workspace. A 409 means the
		 * name is taken; retry with `name` set.
		 */
		install: (hubId: string, cardId: string, name?: string, options?: { silent?: boolean }) =>
			client.post<SkillView>(
				`/hub/skill/${segment(hubId)}/cards/${segment(cardId)}/install`,
				undefined,
				name ? { name } : undefined,
				options,
			),
	},
};
