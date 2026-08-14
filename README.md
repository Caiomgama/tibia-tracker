# Tracker Tibia — 157 → 300

Acompanhamento da meta de level do **Caio Gama** (Yovera, Elder Druid).

A XP vem do highscores oficial do Tibia.com, filtrado por mundo e vocação — que é
onde um personagem fora do top 1000 geral aparece. Uma rotina roda a cada 30 minutos,
lê o ranking e publica os dados; a página lê o que foi publicado.

- **Página:** https://caiomgama.github.io/tibia-tracker/
- **Rotina:** aba *Actions* → *Ler XP do Tibia* (dá para rodar na mão em *Run workflow*)

## Como funciona

O highscores se atualiza de hora em hora — a própria página informa `Last Update`.
Cada ciclo de Server Save tem um *marco* (a primeira leitura depois dele), e a XP de
um dia é a diferença entre dois marcos. Durante o dia, a diferença entre a leitura
mais recente e o marco do ciclo é o parcial.

O Server Save é 10:00 no horário da Alemanha: 08:00 UTC no verão europeu, 09:00 UTC
no inverno. O código trata a virada.

## Rodar no PC

```
python tibia_xp.py serve     # abre em http://localhost:8777, com leitura ao vivo
python tibia_xp.py once      # uma leitura
python tibia_xp.py status    # o que já foi lido
```
