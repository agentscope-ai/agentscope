import { CircleAlert, PlusCircle } from 'lucide-react';
import { useState } from 'react';
import type { ReactNode } from 'react';

import type { MCPClient, MCPView } from '@/api';
import { MCPConfigForm } from '@/components/dialog/MCPConfigForm.tsx';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Checkbox } from '@/components/ui/checkbox.tsx';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog.tsx';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import {
	Item,
	ItemContent,
	ItemDescription,
	ItemGroup,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item.tsx';
import { Spinner } from '@/components/ui/spinner.tsx';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.tsx';
import { useMCPs } from '@/hooks/useMCPs.ts';
import { useTranslation } from '@/i18n/useI18n';

/** Ties the footer's submit button to the form in the other tab. */
const CONFIG_FORM_ID = 'add-mcp-config-form';

interface Props {
	children: ReactNode;
	/** Names already in this workspace, which cannot be picked again. */
	present: Set<string>;
	onAdd: (mcps: MCPClient[]) => Promise<void>;
	onAddFromLibrary: (mcpIds: string[]) => Promise<void>;
}

/**
 * Adds MCPs to a workspace, either from the ones already installed or by
 * pasting a configuration.
 *
 * Two tabs rather than a chooser screen: the paths are peers, and a chooser
 * makes picking wrong cost a round trip back. Installed comes first because
 * reusing something already configured is the common case — pasting a
 * config in is what you do once, per MCP, ever.
 *
 * The body is a fixed height and the footer sits outside the tabs, so
 * switching tabs moves nothing except the content between them.
 */
export function AddMCPDialog({ children, present, onAdd, onAddFromLibrary }: Props) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [tab, setTab] = useState('installed');
	const [picked, setPicked] = useState<Set<string>>(new Set());
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const { mcps, loading } = useMCPs();

	const selectable = mcps.filter((mcp) => !present.has(mcp.name));

	const toggle = (mcp: MCPView) => {
		setPicked((prev) => {
			const next = new Set(prev);
			if (next.has(mcp.id)) next.delete(mcp.id);
			else next.add(mcp.id);
			return next;
		});
	};

	const close = () => {
		setOpen(false);
		setPicked(new Set());
		setError(null);
	};

	const handleAddPicked = async () => {
		setBusy(true);
		setError(null);
		try {
			await onAddFromLibrary([...picked]);
			close();
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setBusy(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
			<DialogTrigger asChild>{children}</DialogTrigger>
			<DialogContent className="sm:max-w-2xl">
				<DialogHeader>
					<DialogTitle>{t('panel.mcp.add')}</DialogTitle>
					<DialogDescription>{t('panel.mcp.addDescription')}</DialogDescription>
				</DialogHeader>

				<Tabs value={tab} onValueChange={setTab}>
					<TabsList className="w-full">
						<TabsTrigger value="installed" className="flex-1">
							{t('panel.mcp.fromInstalled')}
						</TabsTrigger>
						<TabsTrigger value="configure" className="flex-1">
							{t('panel.mcp.fromConfig')}
						</TabsTrigger>
					</TabsList>

					{/* Fixed height on the shared container rather than per
					    tab, so the dialog does not resize as you switch. */}
					<div className="mt-4 h-80 overflow-y-auto scroll-fade">
						<TabsContent value="installed">
							{loading ? (
								<div className="flex justify-center py-10">
									<Spinner />
								</div>
							) : mcps.length === 0 ? (
								<Empty className="border-none py-10">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<PlusCircle />
										</EmptyMedia>
										<EmptyTitle>
											{t('panel.mcp.installedEmptyTitle')}
										</EmptyTitle>
										<EmptyDescription>
											{t('panel.mcp.installedEmptyDescription')}
										</EmptyDescription>
									</EmptyHeader>
								</Empty>
							) : (
								<ItemGroup className="gap-1">
									{mcps.map((mcp) => {
										const already = present.has(mcp.name);
										return (
											<Item key={mcp.id} data-disabled={already || undefined}>
												{/* self-center overrides the base
												    class, which top-aligns media
												    once a description is present. */}
												<Checkbox
													checked={already || picked.has(mcp.id)}
													disabled={already}
													onCheckedChange={() => toggle(mcp)}
												/>

												<ItemMedia>
													<Avatar className="rounded-md">
														<AvatarImage
															src={mcp.icon_url ?? undefined}
															alt={mcp.display_name || mcp.name}
															loading="lazy"
														/>
														<AvatarFallback className="rounded-md">
															{(mcp.display_name || mcp.name)
																.slice(0, 1)
																.toUpperCase()}
														</AvatarFallback>
													</Avatar>
												</ItemMedia>
												<ItemContent>
													<ItemTitle>
														<span className="font-medium">
															{mcp.display_name || mcp.name}
														</span>
														{mcp.author && (
															<span className="text-xs text-muted-foreground">
																@{mcp.author}
															</span>
														)}
													</ItemTitle>
													<ItemDescription className="line-clamp-1">
														{mcp.description}
													</ItemDescription>
												</ItemContent>
											</Item>
										);
									})}
								</ItemGroup>
							)}
						</TabsContent>

						<TabsContent value="configure">
							<MCPConfigForm
								formId={CONFIG_FORM_ID}
								onAdd={onAdd}
								onDone={close}
								onBusyChange={setBusy}
							/>
						</TabsContent>
					</div>
				</Tabs>

				{error && (
					<Alert variant="destructive">
						<CircleAlert />
						{/* One line per MCP that did not land, so the
						    joined message has to keep its newlines. */}
						<AlertDescription className="whitespace-pre-wrap">{error}</AlertDescription>
					</Alert>
				)}

				{/* Outside the tabs on purpose: the footer is the dialog's,
				    not either tab's, so its layout never shifts. */}
				<DialogFooter className="sm:justify-between">
					<span className="self-center text-xs text-muted-foreground">
						{tab === 'installed' && selectable.length > 0
							? t('common.selectedCount', {
									selected: picked.size,
									total: selectable.length,
								})
							: null}
					</span>
					<div className="flex items-center gap-x-2">
						<Button variant="ghost" onClick={close}>
							{t('common.cancel')}
						</Button>
						{tab === 'installed' ? (
							<Button onClick={handleAddPicked} disabled={picked.size === 0 || busy}>
								{busy ? <Spinner /> : <PlusCircle />}
								{t('common.add')}
							</Button>
						) : (
							<Button type="submit" form={CONFIG_FORM_ID} disabled={busy}>
								{busy ? <Spinner /> : <PlusCircle />}
								{t('common.add')}
							</Button>
						)}
					</div>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
