import {
	ArrowRight,
	Check,
	ChevronRight,
	CircleAlert,
	Minus,
	Pencil,
	Trash2,
	X,
} from 'lucide-react';
import * as React from 'react';
import { useNavigate } from 'react-router-dom';

import type { ChannelRecord, ChannelStatus, ChannelTypeSchema, SessionRecord } from '@/api';
import { channelApi } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from '@/components/ui/table';
import { useTranslation } from '@/i18n/useI18n';
import { ChannelStatusBadge } from '@/pages/channel/channel-status';
import { TypeAvatar } from '@/pages/channel/type-avatar';

/** Uppercase muted header cell, matching the credential-page tables. */
function HeadCell({ children, className }: { children?: React.ReactNode; className?: string }) {
	return (
		<TableHead
			className={`h-auto bg-muted px-3 py-2 font-mono text-[9.5px] font-normal uppercase tracking-widest text-text-tertiary ${className ?? ''}`}
		>
			{children}
		</TableHead>
	);
}

interface Props {
	channel: ChannelRecord;
	type?: ChannelTypeSchema;
	status?: ChannelStatus | null;
	agentName: (agentId: string) => string;
	onEdit: () => void;
	onDelete: () => void;
	onClose: () => void;
}

/** Read-only detail view for a channel; edits happen via the dialog. */
export function ChannelDetailPanel({
	channel,
	type,
	status,
	agentName,
	onEdit,
	onDelete,
	onClose,
}: Props) {
	const { t } = useTranslation();
	const navigate = useNavigate();
	const [sessions, setSessions] = React.useState<SessionRecord[]>([]);

	const typeName = type?.display_name ?? channel.channel_type;
	const name = channel.name?.trim() || typeName;
	const model = channel.session.chat_model_config?.model;

	React.useEffect(() => {
		setSessions([]);
		channelApi
			.listSessions(channel.id)
			.then((r) => setSessions(r.sessions))
			.catch(() => setSessions([]));
	}, [channel.id]);

	const bool = (v: boolean) =>
		v ? (
			<Check className="size-4 text-emerald-600" />
		) : (
			<Minus className="size-4 text-muted-foreground/40" />
		);
	const text = (v: React.ReactNode) => <span className="font-mono">{v}</span>;

	// Config rows: label + a value node, all one size.
	const configRows: { label: string; value: React.ReactNode }[] = [
		{ label: t('channel.create.channelType'), value: text(typeName) },
		...(model ? [{ label: t('common.model'), value: text(model) }] : []),
		{ label: t('channel.create.permissionMode'), value: text(channel.session.permission_mode) },
	];
	// Platform options (show tool/thinking, only_at_reply, …) from the schema.
	const cfgProps =
		(
			type?.config_schema as
				| { properties?: Record<string, Record<string, unknown>> }
				| undefined
		)?.properties ?? {};
	for (const [key, def] of Object.entries(cfgProps)) {
		const raw = channel.platform_config[key] ?? def.default;
		configRows.push({
			label: (def.title as string) || key,
			value: typeof raw === 'boolean' ? bool(raw) : text(String(raw ?? '')),
		});
	}

	return (
		<div className="flex h-full flex-col overflow-hidden rounded-[22px] bg-card shadow-panel">
			<div className="flex items-start justify-between gap-3 px-5 pt-5 pb-4">
				<div className="flex min-w-0 items-center gap-3">
					<TypeAvatar type={type} className="size-10 rounded-lg" />
					<div className="min-w-0">
						<div className="truncate text-base font-semibold">{name}</div>
						<div className="mt-0.5 flex items-center gap-2">
							<ChannelStatusBadge enabled={channel.enabled} status={status} />
							<span className="font-mono text-[11px] text-muted-foreground">
								{channel.id.slice(0, 8)}
							</span>
						</div>
					</div>
				</div>
				<div className="flex items-center gap-1">
					<Button
						size="icon-sm"
						variant="ghost"
						onClick={onEdit}
						tooltip={t('common.edit')}
					>
						<Pencil className="size-3.5" />
					</Button>
					<Button
						size="icon-sm"
						variant="ghost"
						onClick={onClose}
						tooltip={t('common.close')}
					>
						<X className="size-3.5" />
					</Button>
				</div>
			</div>

			<div className="flex flex-1 flex-col gap-6 overflow-y-auto px-5 pb-5">
				{status?.last_error && (
					<Alert variant="destructive">
						<CircleAlert />
						<AlertDescription>{status.last_error}</AlertDescription>
					</Alert>
				)}

				<section>
					<div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
						{t('channel.detail.config')}
					</div>
					<p className="mt-1 mb-3 text-[11px] text-muted-foreground">
						{t('channel.detail.configDesc')}
					</p>
					<div className="overflow-hidden rounded-lg border">
						<Table>
							<TableHeader>
								<TableRow>
									<HeadCell>{t('channel.detail.configItem')}</HeadCell>
									<HeadCell>{t('channel.detail.configValue')}</HeadCell>
								</TableRow>
							</TableHeader>
							<TableBody>
								{configRows.map((row, i) => (
									<TableRow key={i}>
										<TableCell className="px-3 py-2 text-xs">
											{row.label}
										</TableCell>
										<TableCell className="px-3 py-2 text-xs">
											{row.value}
										</TableCell>
									</TableRow>
								))}
							</TableBody>
						</Table>
					</div>
				</section>

				<section>
					<div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
						{t('channel.routing')}
						<span className="rounded bg-muted px-2 py-0.5 font-mono text-[10px]">
							{channel.routing.bindings.length}
						</span>
					</div>
					<p className="mt-1 mb-3 text-[11px] text-muted-foreground">
						{t('channel.detail.routingDesc')}
					</p>
					<div className="overflow-hidden rounded-lg border">
						<Table>
							<TableHeader>
								<TableRow>
									<HeadCell>{t('channel.detail.condition')}</HeadCell>
									<HeadCell className="w-6" />
									<HeadCell>{t('common.agent')}</HeadCell>
									<HeadCell>{t('channel.binding.scope')}</HeadCell>
								</TableRow>
							</TableHeader>
							<TableBody>
								{channel.routing.bindings.map((b, i) => {
									const isCatchAll = i === channel.routing.bindings.length - 1;
									return (
										<TableRow key={i}>
											<TableCell className="px-3 py-2 font-mono text-xs">
												{isCatchAll
													? t('channel.binding.fallback')
													: `${b.match_key} = ${b.match_value}`}
											</TableCell>
											<TableCell className="w-6 px-0 text-center">
												<ArrowRight className="inline size-3.5 text-muted-foreground" />
											</TableCell>
											<TableCell className="px-3 py-2 text-xs">
												{agentName(b.agent_id)}
											</TableCell>
											<TableCell className="px-3 py-2 text-xs text-muted-foreground">
												{t(`channel.sessionScope.${b.session_scope}`)}
											</TableCell>
										</TableRow>
									);
								})}
							</TableBody>
						</Table>
					</div>
				</section>

				<section>
					<div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
						{t('channel.sessions.title')}
						<span className="rounded bg-muted px-2 py-0.5 font-mono text-[10px]">
							{sessions.length}
						</span>
					</div>
					<p className="mt-1 mb-3 text-[11px] text-muted-foreground">
						{t('channel.detail.sessionsDesc')}
					</p>
					{sessions.length === 0 ? (
						<p className="text-sm text-muted-foreground">
							{t('channel.sessions.empty')}
						</p>
					) : (
						<div className="overflow-hidden rounded-lg border">
							<Table>
								<TableHeader>
									<TableRow>
										<HeadCell>{t('channel.sessions.title')}</HeadCell>
										<HeadCell>{t('channel.detail.created')}</HeadCell>
										<HeadCell className="w-6" />
									</TableRow>
								</TableHeader>
								<TableBody>
									{sessions.map((s) => (
										<TableRow
											key={s.id}
											className="group cursor-pointer"
											onClick={() => navigate(`/chat/${s.agent_id}/${s.id}`)}
											title={t('channel.sessions.openInChat')}
										>
											<TableCell className="max-w-0 truncate px-3 py-2 text-xs">
												{s.config.name || s.id}
											</TableCell>
											<TableCell className="px-3 py-2 font-mono text-[11px] text-muted-foreground">
												{new Date(s.created_at).toLocaleDateString()}
											</TableCell>
											<TableCell className="w-6 px-0 text-center">
												<ChevronRight className="inline size-3.5 text-muted-foreground opacity-0 transition group-hover:opacity-100" />
											</TableCell>
										</TableRow>
									))}
								</TableBody>
							</Table>
						</div>
					)}
				</section>
			</div>

			<div className="px-5 pt-2 pb-5">
				<Button
					variant="ghost"
					className="w-full text-destructive hover:bg-destructive/10 hover:text-destructive"
					onClick={onDelete}
				>
					<Trash2 className="size-3.5" />
					{t('channel.detail.deleteChannel')}
				</Button>
			</div>
		</div>
	);
}
