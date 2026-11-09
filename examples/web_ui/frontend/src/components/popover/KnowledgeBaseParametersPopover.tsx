import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import { useEffect, useState } from 'react';

import type { JSONSchema, JSONSchemaProperty, SessionKnowledgeConfig } from '@/api';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
	Popover,
	PopoverContent,
	PopoverDescription,
	PopoverHeader,
	PopoverTitle,
	PopoverTrigger,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	/**
	 * The current session-knowledge config. The popover edits its
	 * `parameters` dict; `knowledge_base_ids` is preserved verbatim.
	 * `null` means no KBs are attached — the trigger is then disabled
	 * because there is nothing for the parameters to act on.
	 */
	value: SessionKnowledgeConfig | null;
	/** JSON Schema describing the middleware's tunable parameters. */
	schema: JSONSchema | null;
	/** Persist a new config back to the session. */
	onChange: (next: SessionKnowledgeConfig | null) => void;
	/** Force-disable the trigger regardless of `value` (e.g. no session). */
	disabled?: boolean;
}

type ParamValue = string | number | boolean | null | undefined;

/** Look through `anyOf` to find the property's effective scalar type and
 *  any enum constraint. Pydantic renders `Literal[...]` directly and
 *  `Literal[...] | None` inside the non-null `anyOf` branch. */
function resolve(prop: JSONSchemaProperty): {
	type: string;
	enumValues: unknown[] | null;
} {
	if (prop.type && prop.type !== 'null') {
		return { type: prop.type, enumValues: prop.enum ?? null };
	}
	for (const variant of prop.anyOf ?? []) {
		const v = variant as JSONSchemaProperty;
		if (v.type && v.type !== 'null') {
			return { type: v.type, enumValues: v.enum ?? prop.enum ?? null };
		}
	}
	return { type: 'string', enumValues: prop.enum ?? null };
}

/**
 * Trigger + popover that edits the `KnowledgeBaseMiddleware` parameters
 * for the active session.
 *
 * Schema-driven (entries come from
 * `KnowledgeBaseMiddleware.Parameters.model_json_schema()`) but with a
 * hand-rolled label/control grid so labels stay flush-left and inputs
 * align in a single right-hand column — same pattern as
 * {@link ModelParametersPopover}. Enums render as
 * {@link LlmSelect}-style `DropdownMenu` triggers instead of the native
 * `<Select>` used by the shared {@link SchemaForm}.
 *
 * The trigger is disabled until at least one knowledge base is selected
 * — until then the parameters have nothing to apply to.
 */
export function KnowledgeBaseParametersPopover({
	value,
	schema,
	onChange,
	disabled = false,
}: Props) {
	const { t } = useTranslation();

	// Local draft so number inputs can transiently hold empty strings
	// without racing the persisted `value.parameters`. Kept in sync with
	// the source of truth whenever it changes (session switch, etc.).
	const [paramValues, setParamValues] = useState<Record<string, ParamValue>>(
		(value?.parameters ?? {}) as Record<string, ParamValue>,
	);
	useEffect(() => {
		setParamValues((value?.parameters ?? {}) as Record<string, ParamValue>);
	}, [value]);

	const handleParamChange = (key: string, val: ParamValue) => {
		const next = { ...paramValues, [key]: val };
		if (val === undefined) delete next[key];
		setParamValues(next);
		// Without any selected KB the middleware isn't installed, so a
		// parameter edit has nowhere to land. The trigger is disabled in
		// that state, but guard here as a belt-and-braces check.
		if (!value || value.knowledge_base_ids.length === 0) return;
		onChange({
			knowledge_base_ids: value.knowledge_base_ids,
			parameters: next as Record<string, unknown>,
		});
	};

	const hasSelection = !!value && value.knowledge_base_ids.length > 0;
	const triggerDisabled = disabled || !schema || !hasSelection;

	const entries = Object.entries(schema?.properties ?? {});

	return (
		<Popover>
			<PopoverTrigger asChild>
				<Button
					variant="ghost"
					size="icon-sm"
					disabled={triggerDisabled}
					aria-label={t('panel.knowledge.parametersTitle')}
					title={t('panel.knowledge.parametersTitle')}
				>
					<SlidersHorizontal />
				</Button>
			</PopoverTrigger>
			<PopoverContent
				align="end"
				className="w-80 max-h-[28rem] overflow-y-auto"
				// Stop the popover from forwarding pointer/key events back
				// up to the parent panel header, which would otherwise
				// dismiss focus mid-edit.
				onPointerDown={(e) => e.stopPropagation()}
				onKeyDown={(e) => e.stopPropagation()}
			>
				<PopoverHeader>
					<PopoverTitle>{t('panel.knowledge.parametersTitle')}</PopoverTitle>
					<PopoverDescription>
						{t('panel.knowledge.parametersDescription')}
					</PopoverDescription>
				</PopoverHeader>
				{schema && entries.length > 0 ? (
					<div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-3">
						{entries.map(([key, prop]) => {
							const { type, enumValues } = resolve(prop);
							const id = `kb-mw-${key}`;
							const label = prop.title ?? key.replace(/_/g, ' ');
							const current = paramValues[key];

							let control: React.ReactNode;
							if (type === 'boolean') {
								control = (
									<Switch
										id={id}
										checked={current !== undefined ? !!current : !!prop.default}
										onCheckedChange={(checked) =>
											handleParamChange(key, !!checked)
										}
									/>
								);
							} else if (enumValues) {
								const displayValue =
									current !== undefined && current !== null
										? String(current)
										: prop.default != null
											? String(prop.default)
											: '';
								control = (
									<DropdownMenu>
										<DropdownMenuTrigger asChild>
											<Button
												id={id}
												variant="outline"
												size="sm"
												className="justify-between gap-1 w-full"
											>
												<span className="truncate">{displayValue}</span>
												<ChevronDown className="size-3.5 opacity-50 shrink-0" />
											</Button>
										</DropdownMenuTrigger>
										<DropdownMenuContent
											align="start"
											className="min-w-[var(--radix-dropdown-menu-trigger-width)] max-h-60 overflow-y-auto"
										>
											<DropdownMenuRadioGroup
												value={displayValue}
												onValueChange={(v) => handleParamChange(key, v)}
											>
												{enumValues.map((opt) => (
													<DropdownMenuRadioItem
														key={String(opt)}
														value={String(opt)}
													>
														{String(opt)}
													</DropdownMenuRadioItem>
												))}
											</DropdownMenuRadioGroup>
										</DropdownMenuContent>
									</DropdownMenu>
								);
							} else if (type === 'number' || type === 'integer') {
								control = (
									<Input
										id={id}
										type="number"
										value={
											current === undefined || current === null
												? ''
												: String(current)
										}
										min={prop.minimum}
										max={prop.maximum}
										step={type === 'integer' ? 1 : 'any'}
										placeholder={
											prop.default != null ? String(prop.default) : undefined
										}
										onChange={(e) => {
											const raw = e.target.value;
											if (raw === '') {
												handleParamChange(key, undefined);
												return;
											}
											const parsed =
												type === 'integer'
													? parseInt(raw, 10)
													: parseFloat(raw);
											handleParamChange(
												key,
												Number.isNaN(parsed) ? raw : parsed,
											);
										}}
									/>
								);
							} else {
								control = (
									<Input
										id={id}
										type="text"
										value={
											current === undefined || current === null
												? ''
												: String(current)
										}
										placeholder={
											prop.default != null ? String(prop.default) : undefined
										}
										onChange={(e) => handleParamChange(key, e.target.value)}
									/>
								);
							}

							return (
								<Tooltip key={key}>
									<TooltipTrigger asChild>
										<div className="col-span-2 grid grid-cols-subgrid items-center">
											<Label htmlFor={id} className="whitespace-nowrap">
												{label}
											</Label>
											{control}
										</div>
									</TooltipTrigger>
									{prop.description && (
										<TooltipContent side="left">
											{prop.description}
										</TooltipContent>
									)}
								</Tooltip>
							);
						})}
					</div>
				) : null}
			</PopoverContent>
		</Popover>
	);
}
