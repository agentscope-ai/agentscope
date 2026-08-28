import {
	BookText,
	BotMessageSquare,
	Cable,
	Calendars,
	Compass,
	KeyRound,
	Languages,
	LibraryBig,
	UserRound,
} from 'lucide-react';
import { useOnborda } from 'onborda';
import { useNavigate, useLocation } from 'react-router-dom';

import AgentScope from '@/assets/images/agentscope_white.svg?react';
import MCPSvg from '@/assets/images/mcp.svg?react';
import { CHAT_TOUR_NAME } from '@/components/tour/chatTourSteps';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupContent,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar';
import i18n from '@/i18n';
import { useTranslation } from '@/i18n/useI18n';

export function AppSidebar() {
	const navigate = useNavigate();
	const location = useLocation();
	const { t } = useTranslation();
	const { startOnborda } = useOnborda();

	const handleStartTour = () => {
		if (!location.pathname.startsWith('/chat')) {
			// Page not mounted yet — leave a flag, navigate, and let the
			// ChatTourController auto-trigger after ChatPage mounts.
			sessionStorage.setItem('force_tour', '1');
			navigate('/chat');
		} else {
			startOnborda(CHAT_TOUR_NAME);
		}
	};

	const handleToggleLanguage = () => {
		const next = i18n.language.startsWith('zh') ? 'en' : 'zh';
		i18n.changeLanguage(next);
	};

	return (
		<Sidebar
			collapsible="none"
			className="w-[calc(var(--sidebar-width-icon)+1px)]! bg-transparent"
		>
			<SidebarHeader>
				<div className="flex items-center justify-center size-8 mt-2 rounded-full bg-primary">
					<AgentScope className="size-5 items-center justify-center rounded-lg" />
				</div>
			</SidebarHeader>
			<SidebarContent>
				<SidebarGroup>
					<SidebarGroupContent>
						<SidebarMenu>
							<SidebarMenuItem key={'chat'}>
								<SidebarMenuButton
									tooltip={{ children: t('common.chat'), hidden: false }}
									isActive={
										location.pathname === '/chat' ||
										location.pathname.startsWith('/chat/')
									}
									onClick={() => navigate('/chat')}
									className="justify-center"
								>
									<BotMessageSquare />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.schedule'), hidden: false }}
									isActive={location.pathname === '/schedule'}
									onClick={() => navigate('/schedule')}
									className="justify-center"
								>
									<Calendars />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.channel'), hidden: false }}
									isActive={location.pathname === '/channel'}
									onClick={() => navigate('/channel')}
									className="px-2"
								>
									<Cable />
								</SidebarMenuButton>
							</SidebarMenuItem>
						</SidebarMenu>
					</SidebarGroupContent>
				</SidebarGroup>
				<SidebarGroup>
					<SidebarGroupContent>
						<SidebarMenu>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.credential'), hidden: false }}
									isActive={location.pathname === '/credential'}
									onClick={() => navigate('/credential')}
									className="justify-center"
								>
									<KeyRound />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.mcp-hub'), hidden: false }}
									// Stays lit while browsing a hub under /mcp/:hubId.
									isActive={location.pathname.startsWith('/mcp')}
									onClick={() => navigate('/mcp')}
									className="justify-center"
								>
									<MCPSvg />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.skill-hub'), hidden: false }}
									isActive={location.pathname.startsWith('/skill')}
									onClick={() => navigate('/skill')}
									className="justify-center"
								>
									<BookText />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.knowledge'), hidden: false }}
									isActive={location.pathname === '/knowledge'}
									onClick={() => navigate('/knowledge')}
									className="justify-center"
								>
									<LibraryBig />
								</SidebarMenuButton>
							</SidebarMenuItem>
						</SidebarMenu>
					</SidebarGroupContent>
				</SidebarGroup>
			</SidebarContent>
			<SidebarFooter>
				<SidebarMenu>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{
								children: i18n.language.startsWith('zh')
									? t('common.switchToEn')
									: t('common.switchToZh'),
								hidden: false,
							}}
							onClick={handleToggleLanguage}
							className="justify-center"
						>
							<Languages />
						</SidebarMenuButton>
					</SidebarMenuItem>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{ children: t('tour.trigger'), hidden: false }}
							onClick={handleStartTour}
							className="justify-center"
						>
							<Compass />
						</SidebarMenuButton>
					</SidebarMenuItem>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{ children: t('common.settings'), hidden: false }}
							isActive={location.pathname === '/setup'}
							onClick={() => navigate('/setup')}
							className="justify-center"
						>
							<UserRound />
						</SidebarMenuButton>
					</SidebarMenuItem>
				</SidebarMenu>
			</SidebarFooter>
		</Sidebar>
	);
}
