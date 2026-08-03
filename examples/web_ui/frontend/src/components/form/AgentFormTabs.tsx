import type { AgentSchemaV2Response } from '@/api';
import { AgentBindingFields, type AgentBindings } from '@/components/form/AgentBindingFields';
import {
	AgentFormFields,
	type AgentFormValues,
	type AgentSection,
} from '@/components/form/AgentFormFields';
import type { SchemaFormValue } from '@/components/form/SchemaForm';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.tsx';
import { useTranslation } from '@/i18n/useI18n.ts';

interface Props {
	schema: AgentSchemaV2Response;
	values: AgentFormValues;
	onChange: (section: AgentSection, key: string, value: SchemaFormValue) => void;
	bindings: AgentBindings;
	onBindingsChange: (next: AgentBindings) => void;
	/** Passed through — see {@link AgentBindingFields}. */
	hasWorkspace?: boolean;
}

/**
 * The agent create / edit form, split across tabs.
 *
 * The schema-driven fields alone already overflow the dialog, and what
 * the agent comes with is a separate decision from how it thinks — so
 * the two picker lists get their own tabs rather than being appended to
 * an even longer scroll.
 */
export function AgentFormTabs({
	schema,
	values,
	onChange,
	bindings,
	onBindingsChange,
	hasWorkspace = false,
}: Props) {
	const { t } = useTranslation();

	return (
		<Tabs defaultValue="general">
			<TabsList className="w-full">
				<TabsTrigger value="general" className="flex-1">
					{t('agent-form.tabs.general')}
				</TabsTrigger>
				<TabsTrigger value="mcps" className="flex-1">
					{t('agent-form.tabs.mcps')}
				</TabsTrigger>
				<TabsTrigger value="skills" className="flex-1">
					{t('agent-form.tabs.skills')}
				</TabsTrigger>
			</TabsList>

			{/* Fixed height on the shared container rather than per tab, so
			    the dialog does not resize as you switch.

			    ``-mx-4 px-4``: ``overflow-y-auto`` makes the x axis clip
			    too, which would slice the focus ring off both sides of a
			    full-width input. The padding gives the ring room; the
			    negative margin keeps content at the same x. */}
			<div className="-mx-4 mt-4 h-[60vh] overflow-y-auto px-4 scroll-fade">
				<TabsContent value="general">
					<AgentFormFields schema={schema} values={values} onChange={onChange} />
				</TabsContent>
				<TabsContent value="mcps">
					<AgentBindingFields
						kind="mcp_ids"
						values={bindings}
						onChange={onBindingsChange}
						hasWorkspace={hasWorkspace}
					/>
				</TabsContent>
				<TabsContent value="skills">
					<AgentBindingFields
						kind="skill_ids"
						values={bindings}
						onChange={onBindingsChange}
						hasWorkspace={hasWorkspace}
					/>
				</TabsContent>
			</div>
		</Tabs>
	);
}
