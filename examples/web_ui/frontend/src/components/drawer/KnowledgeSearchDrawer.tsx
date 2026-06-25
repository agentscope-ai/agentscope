import { FlaskConical, Loader2, Search } from 'lucide-react';
import { useState } from 'react';

import type { VectorSearchResult } from '@/api';
import { Badge } from '@/components/ui/badge.tsx';
import { Button } from '@/components/ui/button.tsx';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import { Input } from '@/components/ui/input.tsx';
import { Label } from '@/components/ui/label.tsx';
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from '@/components/ui/sheet.tsx';
import { Textarea } from '@/components/ui/textarea.tsx';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { useTranslation } from '@/i18n/useI18n.ts';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	knowledgeBaseId: string;
	knowledgeBaseName: string;
}

/**
 * Right-side drawer for ad-hoc retrieval testing on a knowledge base.
 * The user types a query, picks `top_k`, and inspects ranked chunks.
 */
export function KnowledgeSearchDrawer({
	open,
	onOpenChange,
	knowledgeBaseId,
	knowledgeBaseName,
}: Props) {
	const { t } = useTranslation();
	const { search } = useKnowledgeBases();
	const [query, setQuery] = useState('');
	const [topK, setTopK] = useState(5);
	const [loading, setLoading] = useState(false);
	const [results, setResults] = useState<VectorSearchResult[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	const handleSearch = async () => {
		const trimmed = query.trim();
		if (!trimmed) return;
		setLoading(true);
		setError(null);
		try {
			const res = await search(knowledgeBaseId, {
				query: trimmed,
				top_k: topK,
			});
			setResults(res.results);
		} catch (e) {
			setError((e as Error).message || String(e));
			setResults(null);
		} finally {
			setLoading(false);
		}
	};

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent className="flex w-full sm:!max-w-[480px] flex-col gap-y-4 p-4">
				<SheetHeader className="px-0">
					<SheetTitle className="flex items-center gap-x-2">
						<FlaskConical className="size-4" />
						{t('knowledge.test.title')}
					</SheetTitle>
					<SheetDescription className="truncate">
						{t('knowledge.test.description', { name: knowledgeBaseName })}
					</SheetDescription>
				</SheetHeader>

				<div className="flex flex-col gap-y-3 px-0">
					<div className="flex flex-col gap-y-1.5">
						<Label htmlFor="kb-test-query" className="text-xs">
							{t('knowledge.test.queryLabel')}
						</Label>
						<Textarea
							id="kb-test-query"
							value={query}
							onChange={(e) => setQuery(e.target.value)}
							placeholder={t('knowledge.test.queryPlaceholder')}
							rows={3}
							disabled={loading}
						/>
					</div>
					<div className="flex items-center gap-x-3">
						<div className="flex items-center gap-x-2">
							<Label htmlFor="kb-test-topk" className="text-xs">
								{t('knowledge.test.topKLabel')}
							</Label>
							<Input
								id="kb-test-topk"
								type="number"
								min={1}
								max={50}
								value={topK}
								onChange={(e) =>
									setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))
								}
								className="w-20"
								disabled={loading}
							/>
						</div>
						<Button
							className="ml-auto"
							size="sm"
							onClick={handleSearch}
							disabled={loading || !query.trim()}
						>
							{loading ? (
								<Loader2 className="size-3.5 animate-spin" />
							) : (
								<Search className="size-3.5" />
							)}
							{t('knowledge.test.searchButton')}
						</Button>
					</div>
					{error && <p className="text-destructive text-sm">{error}</p>}
				</div>

				<div className="flex-1 overflow-y-auto px-0">
					{results === null ? null : results.length === 0 ? (
						<Empty className="border-none py-4">
							<EmptyHeader>
								<EmptyMedia variant="icon">
									<Search />
								</EmptyMedia>
								<EmptyTitle>{t('knowledge.test.emptyTitle')}</EmptyTitle>
								<EmptyDescription>
									{t('knowledge.test.emptyDescription')}
								</EmptyDescription>
							</EmptyHeader>
						</Empty>
					) : (
						<div className="flex flex-col gap-y-3">
							{results.map((hit, idx) => (
								<ResultCard
									key={`${hit.document_id}-${idx}`}
									hit={hit}
									index={idx}
								/>
							))}
						</div>
					)}
				</div>

				<SheetFooter className="px-0">
					<Button variant="ghost" onClick={() => onOpenChange(false)}>
						{t('common.close')}
					</Button>
				</SheetFooter>
			</SheetContent>
		</Sheet>
	);
}

function ResultCard({ hit, index }: { hit: VectorSearchResult; index: number }) {
	const { t } = useTranslation();
	const text =
		hit.chunk.content && typeof hit.chunk.content === 'object' && 'text' in hit.chunk.content
			? String(hit.chunk.content.text ?? '')
			: JSON.stringify(hit.chunk.content);

	return (
		<div className="rounded-md border bg-card p-3 flex flex-col gap-y-2">
			<div className="flex items-center gap-x-2 text-xs text-muted-foreground">
				<Badge variant="secondary" className="font-mono">
					#{index + 1}
				</Badge>
				<Badge variant="outline" className="font-mono">
					{t('knowledge.test.score')}: {hit.score.toFixed(4)}
				</Badge>
				<span className="ml-auto truncate" title={hit.chunk.source}>
					{hit.chunk.source}
				</span>
			</div>
			<p className="text-sm whitespace-pre-wrap break-words">{text}</p>
			<div className="text-xs text-muted-foreground">
				{t('knowledge.test.chunkPosition', {
					index: hit.chunk.chunk_index + 1,
					total: hit.chunk.total_chunks,
				})}
			</div>
		</div>
	);
}
