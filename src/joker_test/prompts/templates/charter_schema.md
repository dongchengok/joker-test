{
	charter_id: int,                              // Charter 唯一 ID
	target_id: int,                               // 对应被测系统的 id
	persona: string,                              // Persona 名称
	target_system: string,                        // 被测系统名称
	target_description: string,                   // 被测系统描述
	load_save: string,                            // 加载哪个存档
	goal: string,                                 // Charter 总目标
	exploration_targets: string[],                // 具体探索目标列表
	heuristics: string[],                         // 应用的启发式（具体到操作）
	expected_behaviors: string[],                 // 正常预期（作为异常对照基线）
	coverage_dimensions: {                        // Coverage Map 四维
		region: string[],                          //   区域
		function: string[],                        //   功能
		operation: string[],                       //   操作
		state: string[]                            //   状态
	},
	time_budget_minutes: int,                     // 时间预算（分钟）
	charter_changes_game_state: "yes" | "no",     // 是否改游戏状态
	severity_threshold: "P0" | "P1" | "P2"        // 上报 Bug 的最低严重度
}
