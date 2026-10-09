import json, ast, sys, os
c=[x for x in json.load(open('cases.json')) if 'only_in' not in x]
for i in map(int, sys.argv[1:]):
    x=c[i]; p=f"work/{x['repo']}/{x['short']}/patch/{x['file']}"
    src=open(p).read(); t=ast.parse(src)
    def find(body,parts):
        for n in body:
            if getattr(n,'name',None)==parts[0]:
                return n if len(parts)==1 else find(n.body,parts[1:])
    n=find(t.body,x['qualname'].split('.'))
    if n.body and isinstance(n.body[0],ast.Expr) and isinstance(getattr(n.body[0],'value',None),ast.Constant) and isinstance(n.body[0].value.value,str): n.body=n.body[1:] or [ast.Pass()]
    print(f"### [{i}] {x['module']}.{x['qualname']}  ({p})"); print(ast.unparse(n)); print()
