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
 * Hub ids are ours and path-safe, but encode anyway so a future one with
 * a space or slash cannot break the URL. Card ids never go in the path:
 * they are opaque strings minted by the hub — ClawHub's search endpoint
 * returns `owner/slug` — and a `/` cannot survive a path segment.
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
			client.get<MCPCard>(`/hub/mcp/${segment(hubId)}/card`, { card_id: cardId }),

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
				`/hub/mcp/${segment(hubId)}/install`,
				body,
				{ card_id: cardId },
				options,
			),
	},

	skill: {
		listHubs: () => client.get<HubInfo[]>('/hub/skill'),

		listCards: (hubId: string, params?: HubBrowseParams) =>
			client.get<SkillHubPage>(`/hub/skill/${segment(hubId)}/cards`, browseQuery(params)),

		/** Unlike the list endpoint, this also fetches the `SKILL.md` body. */
		getCard: (hubId: string, cardId: string) =>
			client.get<SkillCard>(`/hub/skill/${segment(hubId)}/card`, { card_id: cardId }),

		/**
		 * Records the card in the user's library. Like the MCP install this
		 * touches no workspace, and it does not download the archive — that
		 * happens when the skill is put into a workspace. A 409 means the
		 * name is taken; retry with `name` set.
		 */
		install: (hubId: string, cardId: string, name?: string, options?: { silent?: boolean }) =>
			client.post<SkillView>(
				`/hub/skill/${segment(hubId)}/install`,
				undefined,
				name ? { card_id: cardId, name } : { card_id: cardId },
				options,
			),
	},
};
