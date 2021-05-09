let g:completion_trigger_character = ['.', '::']
let g:completion_chain_complete_list = {
			\'default' : {
			\	'default' : [
			\		{'complete_items' : ['lsp', 'snippet']},
			\		{'mode' : 'file'}
			\	],
			\	'comment' : [],
			\	'string' : []
			\	},
			\'vim' : [
			\	{'complete_items': ['snippet']},
			\	{'mode' : 'cmd'}
			\	],
			\'graphql' : [
			\	{'complete_items': ['ts']}
			\	],
			\'bash' : [
			\	{'complete_items': ['ts']}
			\	],
			\'go' : [
			\	{'complete_items': ['ts']}
			\	],
			\'json' : [
			\	{'complete_items': ['ts']}
			\	],
			\'typescript' : [
			\	{'complete_items': ['ts']}
			\	],
			\}
let g:completion_matching_strategy_list = ['exact', 'substring', 'fuzzy']

" Configure buffer completion source
let g:completion_chain_complete_list = [
    \{'complete_items': ['lsp']},
    \{'mode': '<c-p>'},
    \{'mode': '<c-n>'}
\]

