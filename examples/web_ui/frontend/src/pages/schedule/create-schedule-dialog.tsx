import { format } from 'date-fns';
import { ChevronDownIcon, CircleAlert, Loader2, PlusCircle } from 'lucide-react';
import * as React from 'react';

import type { ChatModelConfig, PermissionMode } from '@/api';
import { LlmSelect } from '@/components/select/LlmSelect';
import { PermissionModeSelect } from '@/components/select/PermissionModeSelect';
import { TimezoneSelect } from '@/components/select/TimezoneSelect';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useAgents } from '@/hooks/useAgents';
import { useSchedules } from '@/hooks/useSchedules';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreated?: () => void;
}

type FreqType = 'once' | 'daily' | 'weekly' | 'monthly';

function getDefaultForm() {
	const now = new Date();
	const hh = String(now.getHours()).padStart(2, '0');
	const mm = String(now.getMinutes()).padStart(2, '0');
	return {
		name: '',
		description: '',
		freq: 'daily' as FreqType,
		date: now,
		time: `${hh}:${mm}`,
		endDate: undefined as Date | undefined,
		agentId: '',
		chatModelConfig: null as ChatModelConfig | null,
		permissionMode: 'dont_ask' as PermissionMode,
		timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
		stateful: false,
	};
}

function buildCronExpr(freq: FreqType, time: string, date: Date | undefined): string {
	const [h, m] = time.split(':').map(Number);
	switch (freq) {
		case 'daily':
			return `${m} ${h} * * *`;
		case 'weekly': {
			const weekday = date ? date.getDay() : 1;
			return `${m} ${h} * * ${weekday}`;
		}
		case 'monthly': {
			const monthDay = date ? date.getDate() : 1;
			return `${m} ${h} ${monthDay} * *`;
		}
		default:
			return '';
	}
}

function DatePickerButton({
	date,
	onSelect,
	placeholder,
	disabled,
}: {
	date: Date | undefined;
	onSelect: (d: Date | undefined) => void;
	placeholder: string;
	disabled?: boolean;
}) {
	return (
		<Popover>
			<PopoverTrigger asChild>
				<Button
					variant="outline"
					disabled={disabled}
					size="sm"
					className="w-32 justify-between font-normal"
				>
					{date ? format(date, 'PPP') : placeholder}
					<ChevronDownIcon className="text-muted-foreground opacity-50" />
				</Button>
			</PopoverTrigger>
			<PopoverContent className="w-auto p-0" align="start">
				<Calendar
					mode="single"
					selected={date}
					onSelect={onSelect}
					captionLayout="dropdown"
				/>
			</PopoverContent>
		</Popover>
	);
}

export function CreateScheduleDialog({ open, onOpenChange, onCreated }: Props) {
	const { t } = useTranslation();
	const { create } = useSchedules();
	const { agents } = useAgents();
	const [form, setForm] = React.useState(getDefaultForm);
	const [loading, setLoading] = React.useState(false);
	const [error, setError] = React.useState('');

	React.useEffect(() => {
		if (open) {
			setForm(getDefaultForm());
			setError('');
		}
	}, [open]);

	React.useEffect(() => {
		if (agents.length > 0 && !form.agentId) {
			setForm((prev) => ({ ...prev, agentId: agents[0].id }));
		}
	}, [agents, form.agentId]);

	const set = <K extends keyof ReturnType<typeof getDefaultForm>>(
		key: K,
		value: ReturnType<typeof getDefaultForm>[K],
	) => setForm((prev) => ({ ...prev, [key]: value }));

	const isValid =
		form.name.trim() && !!form.date && !!form.time && !!form.agentId && !!form.chatModelConfig;

	const handleSubmit = async () => {
		setError('');
		if (!isValid) return;
		setLoading(true);
		try {
			let cronExpression: string;

			const d = new Date(form.date!);
			const [h, m] = form.time.split(':').map(Number);
			d.setHours(h, m, 0, 0);

			if (form.freq === 'once') {
				cronExpression = `${m} ${h} ${d.getDate()} ${d.getMonth() + 1} *`;
			} else {
				cronExpression = buildCronExpr(form.freq, form.time, form.date);
			}

			await create({
				name: form.name.trim(),
				description: form.description.trim(),
				cron_expression: cronExpression,
				timezone: form.timezone,
				agent_id: form.agentId,
				chat_model_config: form.chatModelConfig!,
				enabled: true,
				stateful: form.stateful,
				permission_mode: form.permissionMode,
			});
			onCreated?.();
			onOpenChange(false);
		} catch (e) {
			setError(String(e));
		} finally {
			setLoading(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('schedule.createSchedule.title')}</DialogTitle>
					<DialogDescription className="tracking-tight">
						{t('schedule.createSchedule.description')}
					</DialogDescription>
				</DialogHeader>

				<div className="no-scrollbar -mx-4 max-h-[75vh] overflow-y-auto px-4">
					<FieldGroup className="[&>[data-orientation=horizontal]>:last-child]:w-48">
						<Field>
							<FieldLabel>{t('common.name')}</FieldLabel>
							<Input
								className="text-sm h-8"
								value={form.name}
								onChange={(e) => set('name', e.target.value)}
								placeholder={t('schedule.createSchedule.namePlaceholder')}
							/>
						</Field>

						<Field>
							<FieldLabel>{t('schedule.createSchedule.descriptionLabel')}</FieldLabel>
							<Textarea
								value={form.description}
								onChange={(e) => set('description', e.target.value)}
								placeholder={t('schedule.createSchedule.descriptionPlaceholder')}
								className="min-h-[150px]"
							/>
						</Field>

						<div className="flex gap-3">
							<Field className="flex-1">
								<FieldLabel>{t('common.date')}</FieldLabel>
								<DatePickerButton
									date={form.date}
									onSelect={(d) => d && set('date', d)}
									placeholder={t('schedule.pickDate')}
								/>
							</Field>
							<Field className="w-48">
								<FieldLabel>{t('common.time')}</FieldLabel>
								<Input
									type="time"
									value={form.time}
									onChange={(e) => set('time', e.target.value)}
									className="max-h-7 h-7 text-sm appearance-none [&::-webkit-calendar-picker-indicator]:hidden [&::-webkit-calendar-picker-indicator]:appearance-none"
								/>
							</Field>
						</div>

						<Field orientation={'horizontal'}>
							<FieldLabel>{t('schedule.timezone')}</FieldLabel>
							<TimezoneSelect
								className={''}
								value={form.timezone}
								onChange={(v) => set('timezone', v)}
							/>
						</Field>

						<Field orientation={'horizontal'}>
							<FieldLabel>{t('schedule.frequency')}</FieldLabel>
							<Select
								value={form.freq}
								onValueChange={(v) => set('freq', v as FreqType)}
							>
								<SelectTrigger size="sm">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="once">{t('schedule.freqOnce')}</SelectItem>
									<SelectItem value="daily">{t('schedule.freqDaily')}</SelectItem>
									<SelectItem value="weekly">
										{t('schedule.freqWeekly')}
									</SelectItem>
									<SelectItem value="monthly">
										{t('schedule.freqMonthly')}
									</SelectItem>
								</SelectContent>
							</Select>
						</Field>

						<Field orientation={'horizontal'}>
							<FieldLabel
								className={form.freq === 'once' ? 'text-muted-foreground' : ''}
							>
								{t('schedule.endAt')}
							</FieldLabel>
							<DatePickerButton
								date={form.freq === 'once' ? undefined : form.endDate}
								onSelect={(d) => set('endDate', d)}
								placeholder={t('schedule.pickDate')}
								disabled={form.freq === 'once'}
							/>
						</Field>

						<Field orientation={'horizontal'}>
							<FieldLabel>{t('common.agent')}</FieldLabel>
							<Select value={form.agentId} onValueChange={(v) => set('agentId', v)}>
								<SelectTrigger className="w-full" size="sm">
									<SelectValue placeholder={t('common.selectAgent')} />
								</SelectTrigger>
								<SelectContent>
									{agents.map((agent) => (
										<SelectItem key={agent.id} value={agent.id}>
											{agent.data.name}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</Field>

						<Field orientation={'horizontal'}>
							<FieldLabel>{t('common.model')}</FieldLabel>
							<LlmSelect
								value={form.chatModelConfig}
								onChange={(v) => set('chatModelConfig', v)}
							/>
						</Field>

						<Field orientation={'horizontal'}>
							<FieldLabel>{t('schedule.permissionMode')}</FieldLabel>
							<PermissionModeSelect
								className="w-full"
								value={form.permissionMode}
								onChange={(v) => set('permissionMode', v)}
							/>
						</Field>

						<Field>
							<div className="flex flex-row items-center justify-between">
								<div className="flex flex-col gap-y-0.5">
									<FieldLabel>{t('schedule.stateful')}</FieldLabel>
									<span className="text-xs text-muted-foreground">
										{t('schedule.statefulDesc')}
									</span>
								</div>
								<Switch
									checked={form.stateful}
									onCheckedChange={(v) => set('stateful', v)}
								/>
							</div>
						</Field>

						{error && <p className="text-sm text-destructive">{error}</p>}
					</FieldGroup>
				</div>

				<DialogFooter>
					<Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button onClick={handleSubmit} disabled={loading || !isValid}>
						{loading ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<PlusCircle className="size-3.5" />
						)}
						{loading ? t('common.creating') : t('common.create')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
