import { Type, Image, Video, AudioLines } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

// ─── Config ───────────────────────────────────────────────────────────────────

interface ModalityConfig {
	/** The MIME main-type prefix this slot matches, e.g. "image" */
	mainType: string;
	/** Lucide icon to render */
	Icon: LucideIcon;
	/** Fallback label shown in tooltip when no subtypes are present */
	label: string;
	/** text/plain is a special case — matched by exact MIME type */
	exactMatch?: string;
}

const MODALITIES: ModalityConfig[] = [
	{ mainType: 'text', exactMatch: 'text/plain', Icon: Type, label: 'text/plain' },
	{ mainType: 'image', Icon: Image, label: 'image' },
	{ mainType: 'video', Icon: Video, label: 'video' },
	{ mainType: 'audio', Icon: AudioLines, label: 'audio' },
];

// ─── Component ────────────────────────────────────────────────────────────────

interface InputTypeBadgesProps {
	/** Array of MIME types, e.g. ["text/plain", "image/jpeg", "audio/mp3"] */
	inputTypes: string[];
	className?: string;
}

export function InputTypeBadges({ inputTypes, className }: InputTypeBadgesProps) {
	return (
		<TooltipProvider>
			<div className={cn('flex flex-row gap-x-1', className)}>
				{MODALITIES.map(({ mainType, exactMatch, Icon, label }) => {
					// Find matching MIME types from the model's input_types
					const matched = exactMatch
						? inputTypes.filter((t) => t === exactMatch)
						: inputTypes.filter((t) => t.startsWith(mainType + '/'));

					const supported = matched.length > 0;
					const tooltipText = supported ? matched.join(', ') : label;

					return (
						<Tooltip key={mainType}>
							<TooltipTrigger asChild>
								<Icon
									size={18}
									className={supported ? 'stroke-primary' : 'opacity-30'}
								/>
							</TooltipTrigger>
							<TooltipContent>
								<span>{tooltipText}</span>
							</TooltipContent>
						</Tooltip>
					);
				})}
			</div>
		</TooltipProvider>
	);
}
