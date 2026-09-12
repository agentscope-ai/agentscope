import { useState, useEffect, useCallback } from 'react';

import { channelApi } from '../api';
import type { ChannelRecord, CreateChannelRequest, UpdateChannelRequest } from '../api';

export function useChannels() {
	const [channels, setChannels] = useState<ChannelRecord[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const res = await channelApi.list();
			setChannels(res);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	const create = useCallback(
		async (body: CreateChannelRequest) => {
			const res = await channelApi.create(body);
			await refetch();
			return res;
		},
		[refetch],
	);

	const update = useCallback(
		async (channelId: string, body: UpdateChannelRequest) => {
			const res = await channelApi.update(channelId, body);
			await refetch();
			return res;
		},
		[refetch],
	);

	const remove = useCallback(
		async (channelId: string) => {
			await channelApi.delete(channelId);
			await refetch();
		},
		[refetch],
	);

	const enable = useCallback(
		async (channelId: string) => {
			await channelApi.enable(channelId);
			await refetch();
		},
		[refetch],
	);

	const disable = useCallback(
		async (channelId: string) => {
			await channelApi.disable(channelId);
			await refetch();
		},
		[refetch],
	);

	return { channels, loading, error, refetch, create, update, remove, enable, disable };
}
