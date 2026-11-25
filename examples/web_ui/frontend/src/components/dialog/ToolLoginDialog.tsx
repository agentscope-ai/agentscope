import { useEffect, useId, useState, type FormEvent } from 'react';

import { ApiError, client } from '@/api/client';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n/useI18n';

interface LoginView {
	id: string;
	revision: number;
	step: {
		title: string;
		description: string;
		submit_label: string;
		fields: Array<{ name: string; label: string; kind: 'text' | 'secret'; required: boolean }>;
	};
}

export function ToolLoginDialog({
	agentId,
	sessionId,
	change,
}: {
	agentId: string;
	sessionId: string;
	change: number;
}) {
	const [view, setView] = useState<LoginView | null>(null);
	const [reload, setReload] = useState(0);
	const path = '/sessions/' + encodeURIComponent(sessionId) + '/tool-login';

	useEffect(() => {
		const controller = new AbortController();
		void client
			.get<LoginView | null>(path, { agent_id: agentId }, {
				silent: true, signal: controller.signal,
			})
			.then((pending) => {
				if (!controller.signal.aborted) setView(pending);
			})
			.catch(() => {});
		return () => controller.abort();
	}, [agentId, change, path, reload]);

	if (!view) return null;
	return (
		<LoginForm
			key={view.id + ':' + view.revision}
			view={view}
			path={path + '/' + view.id}
			agentId={agentId}
			refresh={() => setReload((count) => count + 1)}
		/>
	);
}

function LoginForm({
	view,
	path,
	agentId,
	refresh,
}: {
	view: LoginView;
	path: string;
	agentId: string;
	refresh: () => void;
}) {
	const { t } = useTranslation();
	const formId = useId();
	const [pending, setPending] = useState(false);
	const [error, setError] = useState('');

	async function perform(method: 'POST' | 'DELETE', body?: unknown) {
		if (pending) return;
		setPending(true);
		setError('');
		try {
			await client.stream(path, {
				method, body, params: { agent_id: agentId }, silent: true, timeoutMs: 45_000,
			});
			refresh();
		} catch (err) {
			if (err instanceof ApiError && (err.status === 404 || err.status === 409)) refresh();
			setError(err instanceof ApiError ? err.detail : t('chat.toolLogin.submitError'));
		} finally {
			setPending(false);
		}
	}

	function submit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const form = event.currentTarget;
		const values = Object.fromEntries(new FormData(form));
		form.reset();
		void perform('POST', { revision: view.revision, values });
	}

	return (
		<Dialog open>
			<DialogContent
				showCloseButton={false}
				onEscapeKeyDown={(event) => event.preventDefault()}
				onInteractOutside={(event) => event.preventDefault()}
			>
				<DialogHeader>
					<DialogTitle>{view.step.title}</DialogTitle>
					<DialogDescription>{view.step.description}</DialogDescription>
				</DialogHeader>
				<form onSubmit={submit} autoComplete="off" className="grid gap-4">
					{view.step.fields.map((field, index) => {
						const id = formId + '-' + index;
						return (
							<div key={field.name} className="grid gap-2">
								<Label htmlFor={id}>{field.label}</Label>
								<Input
									id={id}
									name={field.name}
									type={field.kind === 'secret' ? 'password' : 'text'}
									autoComplete="off"
									required={field.required}
									disabled={pending}
									maxLength={4096}
								/>
							</div>
						);
					})}
					{error && <p role="alert" className="text-destructive">{error}</p>}
					<DialogFooter>
						<Button type="button" variant="outline" disabled={pending} onClick={() => void perform('DELETE')}>
							{t('chat.toolLogin.cancel')}
						</Button>
						<Button type="submit" disabled={pending}>
							{pending ? t('chat.toolLogin.working') : view.step.submit_label}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
