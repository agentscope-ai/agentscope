import { Mic, MicOff, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface VoiceModeInputProps {
	/** Whether the microphone is actively streaming audio. */
	micActive: boolean;
	/** Whether the WebSocket connection is established. */
	connected: boolean;
	/** Toggle mic mute/unmute within voice mode. */
	onToggleMic: () => void;
	className?: string;
}

export function VoiceModeInput({
	micActive,
	connected,
	onToggleMic,
	className,
}: VoiceModeInputProps) {
	const { t } = useTranslation();
	const connecting = !connected;

	return (
		<div
			className={cn(
				'flex flex-col items-center justify-center gap-3 rounded-2xl border bg-background p-6',
				className,
			)}
		>
			{connecting ? (
				<>
					<Loader2 className="size-8 animate-spin text-muted-foreground" />
					<span className="text-sm text-muted-foreground">
						{t('voiceMode.connecting')}
					</span>
				</>
			) : (
				<>
					<Button
						size="lg"
						variant={micActive ? 'destructive' : 'default'}
						onClick={onToggleMic}
						className="size-16 rounded-full"
					>
						{micActive ? (
							<Mic className="size-6 animate-pulse" />
						) : (
							<MicOff className="size-6" />
						)}
					</Button>
					<span className="text-sm text-muted-foreground">
						{micActive ? t('voiceMode.listening') : t('voiceMode.muted')}
					</span>
				</>
			)}
		</div>
	);
}
