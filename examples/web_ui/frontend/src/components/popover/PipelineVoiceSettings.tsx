import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import { useRef } from 'react';

import type { ChatModelConfig, PipelineVoiceConfig } from '@/api';
import {
	ASRModelMenu,
	ModelParametersPopover,
	TTSModelMenu,
} from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useAvailableASRModels } from '@/hooks/useAvailableASRModels';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	config: PipelineVoiceConfig | null;
	chatModel: ChatModelConfig | null;
	onChange: (patch: Partial<PipelineVoiceConfig>) => void;
	disabled: boolean;
}

export function PipelineVoiceSettings({ config, chatModel, onChange, disabled }: Props) {
	const { t } = useTranslation();
	const contentRef = useRef<HTMLDivElement>(null);
	const { groups } = useAvailableModels();
	const { groups: asrGroups } = useAvailableASRModels();
	const model = config?.chat_model_config ?? chatModel;
	const modelCard = model
		? ((groups[model.type] ?? [])
				.find((item) => item.credential.id === model.credential_id)
				?.models.find(
					(card) =>
						card.name === model.model &&
						(!model.model_class || card.model_class === model.model_class),
				) ?? null)
		: null;

	return (
		<Popover>
			<PopoverTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					disabled={disabled}
					aria-label={t('pipelineVoice.models')}
					className="gap-1.5 text-muted-foreground hover:text-foreground"
				>
					<SlidersHorizontal className="size-3.5" />
					{t('pipelineVoice.modelSettings')}
					<ChevronDown className="size-3" />
				</Button>
			</PopoverTrigger>
			<PopoverContent
				collisionPadding={8}
				align="start"
				className="w-80 max-w-[calc(100vw-2rem)] space-y-3 p-3"
				ref={contentRef}
				tabIndex={-1}
				onOpenAutoFocus={(event) => {
					event.preventDefault();
					contentRef.current?.focus();
				}}
			>
				<div className="flex items-center justify-between gap-2">
					<span className="text-sm font-medium">{t('pipelineVoice.models')}</span>
					<ModelParametersPopover
						selectedModel={model}
						modelCard={modelCard}
						onChange={(parameters) => {
							if (model) onChange({ chat_model_config: { ...model, parameters } });
						}}
						selectedASRModel={config?.asr_model_config ?? null}
						onASRChange={(value) => onChange({ asr_model_config: value })}
						selectedTTSModel={config?.tts_model_config ?? null}
						onTTSChange={(value) => onChange({ tts_model_config: value })}
						disabled={disabled}
						idPrefix="pipeline"
						triggerLabel={t('pipelineVoice.parameters')}
					/>
				</div>
				<div className="space-y-1">
					<ASRModelMenu
						standalone
						groups={asrGroups}
						selectedASRModel={config?.asr_model_config ?? null}
						onASRChange={(value) => onChange({ asr_model_config: value })}
						disabled={disabled}
						className="w-full max-w-none justify-between gap-2 sm:max-w-none"
					/>
					<div className="flex items-center gap-2 px-2">
						<span className="w-6 shrink-0 text-[10px] font-medium text-muted-foreground">
							LLM
						</span>
						<LlmSelect
							value={config?.chat_model_config ?? null}
							onChange={(value) => onChange({ chat_model_config: value })}
							allowClear
							clearLabel={t('pipelineVoice.followChat')}
							placeholder={t('pipelineVoice.followChat')}
							aria-label={t('pipelineVoice.llm')}
							disabled={disabled}
							variant="ghost"
							className="min-w-0 flex-1 px-0 text-xs text-muted-foreground"
						/>
					</div>
					<TTSModelMenu
						standalone
						selectedTTSModel={config?.tts_model_config ?? null}
						onTTSChange={(value) => onChange({ tts_model_config: value })}
						disabled={disabled}
						className="w-full max-w-none justify-between gap-2 sm:max-w-none"
					/>
				</div>
				{!config?.chat_model_config && (
					<p className="px-2 text-xs text-muted-foreground">
						{t('pipelineVoice.followingModel', {
							model: modelCard?.label ?? chatModel?.model ?? '—',
						})}
					</p>
				)}
			</PopoverContent>
		</Popover>
	);
}
