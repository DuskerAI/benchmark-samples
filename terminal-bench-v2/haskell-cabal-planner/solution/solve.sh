#!/bin/bash
set -euo pipefail

cat > /app/Main.hs <<'HS'
module Main where

import Data.Char (isDigit)
import Data.List (intercalate, nub, sort, sortBy)
import qualified Data.Map.Strict as M
import qualified Data.Set as S
import System.Environment (getArgs)
import System.Exit (die)

data Op = Eq | Gt | Ge | Lt | Le deriving (Eq, Show)
data C = C Op [Int] deriving (Eq, Show)
data Req = Req String [C] deriving (Eq, Show)
data Flag = Flag String Bool [Req] [Req] deriving (Eq, Show)
data Pkg = Pkg { pn :: String, pv :: String, ds :: [Req], fs :: [Flag], ps :: [(String,String)], ns :: [(String,String)], cs :: [Req] } deriving (Eq, Show)
type Repo = M.Map (String,String) Pkg
type Choice = M.Map String (String, [(String,Bool)])
type Locks = M.Map String String

emptyPkg :: String -> String -> Pkg
emptyPkg n v = Pkg n v [] [] [] [] []

main :: IO ()
main = do
  args <- getArgs
  case args of
    [inp,out] -> do
      txt <- readFile inp
      let (repo, roots, locks) = loadRepo txt
      case solve repo roots locks of
        Nothing -> die "no valid build plan"
        Just plan -> writeFile out (render repo plan)
    _ -> die "usage: cabal_plan <repo> <output>"

loadRepo :: String -> (Repo, [Req], Locks)
loadRepo = foldl step (M.empty, [], M.empty) . filter (not . null) . map words . lines
  where
    upd k f m = M.alter (Just . f . maybe (emptyPkg (fst k) (snd k)) id) k m
    step (m,rs,locks) xs = case xs of
      ["pkg",n,v] -> (M.insertWith (\_ old -> old) (n,v) (emptyPkg n v) m, rs, locks)
      ["dep",n,v,reqs] -> (upd (n,v) (\p -> p { ds = ds p ++ reqList reqs }) m, rs, locks)
      ["flag",n,v,fl,def,t,f] -> (upd (n,v) (\p -> p { fs = fs p ++ [Flag fl (def == "True") (reqList t) (reqList f)] }) m, rs, locks)
      ["provide",n,v,mo,abi] -> (upd (n,v) (\p -> p { ps = ps p ++ [(mo,abi)] }) m, rs, locks)
      ["need",n,v,mo,abi] -> (upd (n,v) (\p -> p { ns = ns p ++ [(mo,abi)] }) m, rs, locks)
      ["conflict",n,v,reqs] -> (upd (n,v) (\p -> p { cs = cs p ++ reqList reqs }) m, rs, locks)
      ["lock",n,v] -> (m, rs, M.insert n v locks)
      ["root",r] -> (m, rs ++ [parseReq r], locks)
      _ -> (m,rs,locks)

reqList :: String -> [Req]
reqList "-" = []
reqList s = map parseReq (split ',' s)

parseReq :: String -> Req
parseReq s = Req name (map parseC parts)
  where
    (name, tailS) = span (\c -> c /= '<' && c /= '>' && c /= '=') s
    parts = case tailS of "" -> []; _ -> split ',' tailS
    parseC x = case x of
      ('>':'=':v) -> C Ge (ver v)
      ('<':'=':v) -> C Le (ver v)
      ('>':v) -> C Gt (ver v)
      ('<':v) -> C Lt (ver v)
      ('=':v) -> C Eq (ver v)
      _ -> C Eq (ver x)

split :: Char -> String -> [String]
split c s = case break (== c) s of
  (a,[]) -> [a]
  (a,_:b) -> a : split c b

ver :: String -> [Int]
ver [] = []
ver s = map read (split '.' (takeWhile (\x -> isDigit x || x == '.') s))

norm :: [Int] -> [Int]
norm xs = take 8 (xs ++ repeat 0)

cmpV :: String -> String -> Ordering
cmpV a b = compare (norm (ver a)) (norm (ver b))

okReq :: Req -> Pkg -> Bool
okReq (Req n cs) p = n == pn p && all ok cs
  where
    vv = norm (ver (pv p))
    ok (C o x) = case o of
      Eq -> vv == norm x; Gt -> vv > norm x; Ge -> vv >= norm x; Lt -> vv < norm x; Le -> vv <= norm x

solve :: Repo -> [Req] -> Locks -> Maybe Choice
solve repo roots locks = best [p | p <- search roots M.empty, moduleOK p, conflictOK p]
  where
    candidates (Req n _) = sortBy (\a b -> cmpV (pv b) (pv a)) [p | p <- M.elems repo, pn p == n, maybe True (== pv p) (M.lookup n locks)]
    search [] chosen = [chosen]
    search (r@(Req n _):todo) chosen = case M.lookup n chosen of
      Just (v,_) -> case M.lookup (n,v) repo of
        Just p | okReq r p -> search todo chosen
        _ -> []
      Nothing -> concatMap addOne (filter (okReq r) (candidates r))
      where
        addOne p = concatMap (withFlags p) (flagCombos (fs p))
        withFlags p vals = search (todo ++ ds p ++ active (fs p) vals) (M.insert (pn p) (pv p, vals) chosen)
    active flags vals = concat [if val then t else f | Flag fl _ t f <- flags, Just val <- [lookup fl vals]]
    flagCombos [] = [[]]
    flagCombos (Flag fl def _ _ : rest) = [(fl,v):xs | v <- [def, not def], xs <- flagCombos rest]
    moduleOK chosen = all needOK selected
      where
        selected = pkgs repo chosen
        have = S.fromList [(m,a) | p <- selected, (m,a) <- ps p]
        needOK p = all (`S.member` have) (ns p)
    conflictOK chosen = all pkgOK selected
      where
        selected = pkgs repo chosen
        pkgOK p = all (not . matchesAnyOther p) (cs p)
        matchesAnyOther p r = any (okReq r) [q | q <- selected, pn q /= pn p]
    best [] = Nothing
    best xs = Just (foldl1 better xs)
    better a b | score a <= score b = a | otherwise = b
    score chosen = (versionRank, deviations, ids)
      where
        names = sort (M.keys chosen)
        versionRank = [map negate (norm (ver v)) | n <- names, let Just (v,_) = M.lookup n chosen]
        deviations = sum [length [() | Flag fl def _ _ <- fs p, Just val <- [lookup fl vals], val /= def] | p <- pkgs repo chosen, let Just (_, vals) = M.lookup (pn p) chosen]
        ids = [n ++ "-" ++ v | (n,(v,_)) <- M.toList chosen]

pkgs :: Repo -> Choice -> [Pkg]
pkgs repo chosen = sortBy (\a b -> compare (pn a, pv a) (pn b, pv b)) [p | (n,(v,_)) <- M.toList chosen, Just p <- [M.lookup (n,v) repo]]

render :: Repo -> Choice -> String
render repo chosen = "{\"packages\":[" ++ intercalate "," (map pkgJson selected) ++ "],\"build_order\":" ++ arr2 (buildOrder selected) ++ "}\n"
  where
    selected = pkgs repo chosen
    flagsFor p = maybe [] snd (M.lookup (pn p) chosen)
    depIds p = sort . nub $ [n ++ "-" ++ v | Req n _ <- ds p ++ active p, Just (v,_) <- [M.lookup n chosen]]
    active p = concat [if val then t else f | Flag fl _ t f <- fs p, Just val <- [lookup fl (flagsFor p)]]
    pkgJson p = "{\"name\":"++q(pn p)++",\"version\":"++q(pv p)++",\"id\":"++q(pid p)++",\"flags\":"++flagJson (flagsFor p)++",\"depends\":"++arr(depIds p)++",\"provides\":"++pairs (ps p)++",\"needs\":"++pairs (ns p)++"}"
    flagJson xs = "[" ++ intercalate "," ["{\"name\":"++q k++",\"value\":"++ if v then "true}" else "false}" | (k,v) <- sort xs] ++ "]"
    pairs xs = "[" ++ intercalate "," ["{\"module\":"++q m++",\"abi\":"++q a++"}" | (m,a) <- sort xs] ++ "]"
    depEdges = M.fromList [(pid p, depIds p) | p <- selected]
    buildOrder xs = orderGroups groups orderEdges
      where
        ids = map pid xs
        reach a b = b `S.member` closure S.empty [a]
        closure seen [] = seen
        closure seen (x:todo)
          | x `S.member` seen = closure seen todo
          | otherwise = closure (S.insert x seen) (M.findWithDefault [] x depEdges ++ todo)
        groups = uniqueGroups [sort [b | b <- ids, reach a b && reach b a] | a <- ids]
        providerIds need = [pid p | p <- xs, need `elem` ps p]
        needOrder p provider = if reach (pid p) provider || reach provider (pid p) then [] else [provider]
        needEdges = M.fromListWith (++) [(pid p, needOrder p provider) | p <- xs, need <- ns p, provider <- providerIds need]
        orderEdges = M.unionWith (++) depEdges needEdges
    pid p = pn p ++ "-" ++ pv p
    arr xs = "[" ++ intercalate "," (map q xs) ++ "]"
    arr2 xs = "[" ++ intercalate "," (map arr xs) ++ "]"
    q s = show s

uniqueGroups :: [[String]] -> [[String]]
uniqueGroups = foldl (\acc g -> if g `elem` acc then acc else acc ++ [g]) [] . sort

orderGroups :: [[String]] -> M.Map String [String] -> [[String]]
orderGroups groups edges = go groups []
  where
    groupOf x = head [g | g <- groups, x `elem` g]
    deps g = nub [groupOf d | x <- g, d <- M.findWithDefault [] x edges, groupOf d /= g]
    ready remaining g = all (`notElem` remaining) (deps g)
    go [] out = out
    go remaining out = case sortBy (\a b -> compare (head a) (head b)) (filter (ready remaining) remaining) of
      [] -> out ++ sortBy (\a b -> compare (head a) (head b)) remaining
      (g:_) -> go (filter (/= g) remaining) (out ++ [sort g])
HS

cd /app
ghc -O2 Main.hs -o cabal_plan
